"""Photo objects round-trip through the S3 interface as a tar (row 36 backups).

The same export and import are the migration path to another S3 server (row 41).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.services.object_backup import (
    BackupStats,
    bucket_digest,
    export_bucket_tar,
    import_bucket_tar,
)

MANAGE = Path(__file__).resolve().parents[4] / "scripts" / "manage.py"

OBJECTS = {
    "u1/p1.bin": b"\x00\x01full-bytes",
    "u1/p1.display.bin": b"display",
    "u 2/fötö/deep/a/b/c.bin": b"unicode and spaces",
    "avatars/x.bin": b"",
}


@pytest.fixture
def bucket(s3_client):
    name = f"bk-{uuid.uuid4().hex[:12]}"
    s3_client.create_bucket(Bucket=name)
    return name


def _put_all(s3, bucket: str, objects: dict[str, bytes]) -> None:
    for key, body in objects.items():
        s3.put_object(Bucket=bucket, Key=key, Body=body)


def _contents(s3, bucket: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
    return out


def _fresh_name() -> str:
    return f"bk-{uuid.uuid4().hex[:12]}"


def test_round_trip_keys_and_bytes(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, OBJECTS)
    buf = io.BytesIO()

    stats = export_bucket_tar(s3_client, bucket, buf)

    assert stats == BackupStats(objects=4, bytes=sum(len(b) for b in OBJECTS.values()))
    target = _fresh_name()
    s3_client.create_bucket(Bucket=target)
    buf.seek(0)
    assert import_bucket_tar(s3_client, target, buf) == stats
    assert _contents(s3_client, target) == OBJECTS


def test_listing_pages(s3_client, bucket) -> None:
    five = {f"k/{i}.bin": bytes([i]) * (i + 1) for i in range(5)}
    _put_all(s3_client, bucket, five)
    buf = io.BytesIO()

    stats = export_bucket_tar(s3_client, bucket, buf, page_size=2)

    assert stats.objects == 5
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r|") as tar:
        assert sorted(m.name for m in tar) == sorted(five)


def test_empty_bucket(s3_client, bucket) -> None:
    buf = io.BytesIO()

    assert export_bucket_tar(s3_client, bucket, buf) == BackupStats(0, 0)

    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r|") as tar:
        assert list(tar) == []


def test_import_creates_missing_bucket(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, {"a.bin": b"a"})
    buf = io.BytesIO()
    export_bucket_tar(s3_client, bucket, buf)
    target = _fresh_name()

    buf.seek(0)
    import_bucket_tar(s3_client, target, buf)

    assert _contents(s3_client, target) == {"a.bin": b"a"}


def test_import_overwrites_same_key_keeps_others(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, {"same.bin": b"new"})
    buf = io.BytesIO()
    export_bucket_tar(s3_client, bucket, buf)
    target = _fresh_name()
    s3_client.create_bucket(Bucket=target)
    _put_all(s3_client, target, {"same.bin": b"old", "other.bin": b"kept"})

    buf.seek(0)
    import_bucket_tar(s3_client, target, buf)

    assert _contents(s3_client, target) == {"same.bin": b"new", "other.bin": b"kept"}


def test_import_rejects_non_file_member(s3_client) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        link = tarfile.TarInfo("evil")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    buf.seek(0)

    with pytest.raises(ValueError):
        import_bucket_tar(s3_client, _fresh_name(), buf)


@pytest.fixture
def manage(monkeypatch, s3_client):
    spec = importlib.util.spec_from_file_location("manage_cli_photos", MANAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["manage_cli_photos"] = module
    spec.loader.exec_module(module)
    return module


def test_cli_export_import_json_stats(manage, monkeypatch, s3_client, bucket) -> None:
    _put_all(s3_client, bucket, OBJECTS)
    monkeypatch.setattr(manage, "_s3_and_bucket", lambda: (s3_client, bucket))
    runner = CliRunner()  # click 8.2+ keeps stderr separate

    exported = runner.invoke(manage.cli, ["photos", "export"])

    assert exported.exit_code == 0, exported.stderr
    assert json.loads(exported.stderr.strip().splitlines()[-1]) == {
        "objects": 4,
        "bytes": sum(len(b) for b in OBJECTS.values()),
    }
    target = _fresh_name()
    monkeypatch.setattr(manage, "_s3_and_bucket", lambda: (s3_client, target))

    imported = runner.invoke(manage.cli, ["photos", "import"], input=exported.stdout_bytes)

    assert imported.exit_code == 0, imported.stderr
    assert json.loads(imported.stderr.strip().splitlines()[-1])["objects"] == 4
    assert _contents(s3_client, target) == OBJECTS


# ── Exact copies and digests (row 41: the storage switch) ───────────────────


def _tar_of(objects: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for key, body in objects.items():
            info = tarfile.TarInfo(key)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    buf.seek(0)
    return buf


def test_import_exact_removes_objects_not_in_tar(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, {"a": b"old a", "b": b"old b", "stale": b"gone"})

    tar = _tar_of({"a": b"new a", "b": b"new b"})
    stats = import_bucket_tar(s3_client, bucket, tar, exact=True)

    assert _contents(s3_client, bucket) == {"a": b"new a", "b": b"new b"}
    assert stats.deleted == 1
    assert stats.objects == 2


def test_import_not_exact_keeps_others(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, {"a": b"old a", "stale": b"kept"})

    stats = import_bucket_tar(s3_client, bucket, _tar_of({"a": b"new a"}))

    assert _contents(s3_client, bucket) == {"a": b"new a", "stale": b"kept"}
    assert stats.deleted == 0


def test_import_exact_into_empty_bucket(s3_client) -> None:
    target = _fresh_name()

    stats = import_bucket_tar(s3_client, target, _tar_of(OBJECTS), exact=True)

    assert stats.deleted == 0
    assert _contents(s3_client, target) == OBJECTS


def test_import_exact_deletes_more_than_one_page(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, {f"stale/{i:04d}": b"x" for i in range(1003)})

    stats = import_bucket_tar(s3_client, bucket, _tar_of({"keep": b"k"}), exact=True)

    assert stats.deleted == 1003
    assert _contents(s3_client, bucket) == {"keep": b"k"}


def test_bucket_digest_equal_for_identical_buckets_and_differs_on_size(s3_client, bucket) -> None:
    _put_all(s3_client, bucket, OBJECTS)
    copy = _fresh_name()
    out = io.BytesIO()
    export_bucket_tar(s3_client, bucket, out)
    out.seek(0)
    import_bucket_tar(s3_client, copy, out)

    first, second = bucket_digest(s3_client, bucket), bucket_digest(s3_client, copy)
    assert first == second
    assert first["objects"] == 4
    assert first["bytes"] == sum(len(b) for b in OBJECTS.values())
    assert len(first["digest"]) == 64

    s3_client.put_object(Bucket=copy, Key="u1/p1.bin", Body=b"different length")
    assert bucket_digest(s3_client, copy)["digest"] != first["digest"]


def test_bucket_digest_of_empty_bucket(s3_client, bucket) -> None:
    assert bucket_digest(s3_client, bucket) == {
        "objects": 0,
        "bytes": 0,
        "digest": hashlib.sha256(b"").hexdigest(),
    }


def test_cli_photos_digest_prints_one_json_line(manage, monkeypatch, s3_client, bucket) -> None:
    _put_all(s3_client, bucket, OBJECTS)
    monkeypatch.setattr(manage, "_s3_and_bucket", lambda: (s3_client, bucket))

    result = CliRunner().invoke(manage.cli, ["photos", "digest"])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == bucket_digest(s3_client, bucket)


def test_cli_import_exact_reports_deleted(manage, monkeypatch, s3_client, bucket) -> None:
    _put_all(s3_client, bucket, {"a": b"1", "stale": b"2"})
    monkeypatch.setattr(manage, "_s3_and_bucket", lambda: (s3_client, bucket))

    result = CliRunner().invoke(
        manage.cli, ["photos", "import", "--exact"], input=_tar_of({"a": b"1"}).getvalue()
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stderr.strip().splitlines()[-1]) == {
        "objects": 1,
        "bytes": 1,
        "deleted": 1,
    }
    assert _contents(s3_client, bucket) == {"a": b"1"}
