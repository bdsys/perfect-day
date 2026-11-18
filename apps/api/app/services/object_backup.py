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

import tarfile
from dataclasses import dataclass
from typing import BinaryIO

from botocore.exceptions import ClientError

CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class BackupStats:
    objects: int
    bytes: int  # noqa: A003  # total object bytes


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


def import_bucket_tar(s3, bucket: str, inp: BinaryIO) -> BackupStats:
    """Put every file in the tar on *inp* into *bucket*, creating it if missing.

    Objects with the same key are overwritten; others are left in place. Any
    member that is not a regular file raises ValueError (before it is written).
    """
    _ensure_bucket(s3, bucket)
    count = 0
    total = 0
    with tarfile.open(fileobj=inp, mode="r|") as tar:
        for member in tar:
            if not member.isreg():
                raise ValueError(f"not a regular file in the photos tar: {member.name!r}")
            data = tar.extractfile(member)
            assert data is not None  # regular files always have data
            body = data.read()
            s3.put_object(Bucket=bucket, Key=member.name, Body=body, ContentType=CONTENT_TYPE)
            count += 1
            total += len(body)
    return BackupStats(objects=count, bytes=total)
