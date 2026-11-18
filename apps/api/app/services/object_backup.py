"""Copy every object in a bucket to and from a tar stream, through the S3 interface.

Used by the nightly backup (`manage.py photos export`), by restores
(`manage.py photos import`), and as the path for moving photos to another
S3-compatible server (tracker row 41). Talking S3 rather than copying the
server's data folder keeps backups independent of the server's on-disk format.

Tar layout: one regular file per object, member name = object key, data = the
object's bytes. Every object the app writes is `application/octet-stream`
(services/photos.py put_object_bytes), so no other metadata travels.
"""

from __future__ import annotations

import hashlib
import tarfile
from dataclasses import dataclass
from typing import BinaryIO

from botocore.exceptions import ClientError

CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class BackupStats:
    objects: int
    bytes: int  # noqa: A003  # total object bytes
    deleted: int = 0  # objects an exact import removed from the destination


def export_bucket_tar(s3, bucket: str, out: BinaryIO, page_size: int = 1000) -> BackupStats:
    """Write every object in *bucket* to *out* as a stream-mode tar."""
    count = 0
    total = 0
    paginator = s3.get_paginator("list_objects_v2")
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for page in paginator.paginate(Bucket=bucket, PaginationConfig={"PageSize": page_size}):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                response = s3.get_object(Bucket=bucket, Key=key)
                info = tarfile.TarInfo(key)
                info.size = response["ContentLength"]
                tar.addfile(info, response["Body"])
                count += 1
                total += info.size
    return BackupStats(objects=count, bytes=total)


def _ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)


def _all_keys(s3, bucket: str) -> list[str]:
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def import_bucket_tar(s3, bucket: str, inp: BinaryIO, exact: bool = False) -> BackupStats:
    """Put every file in the tar on *inp* into *bucket*, creating it if missing.

    Objects with the same key are overwritten. Others are left in place, unless
    *exact*: then every object the tar did not hold is deleted afterwards, so the
    bucket ends as an exact copy (the storage switch, row 41: a photo deleted on one
    server must not survive on the other). Any member that is not a regular file
    raises ValueError (before it is written, and before anything is deleted).
    """
    _ensure_bucket(s3, bucket)
    count = 0
    total = 0
    imported: set[str] = set()
    with tarfile.open(fileobj=inp, mode="r|") as tar:
        for member in tar:
            if not member.isreg():
                raise ValueError(f"not a regular file in the photos tar: {member.name!r}")
            data = tar.extractfile(member)
            assert data is not None  # regular files always have data
            body = data.read()
            s3.put_object(Bucket=bucket, Key=member.name, Body=body, ContentType=CONTENT_TYPE)
            imported.add(member.name)
            count += 1
            total += len(body)
    deleted = 0
    if exact:
        stale = [key for key in _all_keys(s3, bucket) if key not in imported]
        for start in range(0, len(stale), 1000):  # DeleteObjects takes at most 1000
            batch = stale[start : start + 1000]
            response = s3.delete_objects(
                Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True}
            )
            if response.get("Errors"):
                raise RuntimeError(f"could not delete {len(response['Errors'])} stale objects")
            deleted += len(batch)
    return BackupStats(objects=count, bytes=total, deleted=deleted)


def bucket_digest(s3, bucket: str) -> dict:
    """Object count, total bytes and a sha256 over the sorted "key<TAB>size" lines.

    Two buckets with the same digest hold the same keys with the same sizes; the
    storage switch compares source and destination this way before using either.
    """
    sizes: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            sizes[obj["Key"]] = obj["Size"]
    digest = hashlib.sha256()
    for key in sorted(sizes):
        digest.update(f"{key}\t{sizes[key]}\n".encode())
    return {"objects": len(sizes), "bytes": sum(sizes.values()), "digest": digest.hexdigest()}
