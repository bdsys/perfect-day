"""The compose files wire photo storage the way the Garage design says (tracker row 41).

These read the files only; nothing is started.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
COMPOSE_FILES = sorted(REPO.glob("docker-compose*.yml"))


def _services() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]


def test_garage_image_pinned():
    assert _services()["garage"]["image"] == "dxflrs/garage:v2.5.0"


def test_garage_health_check_needs_no_shell():
    assert _services()["garage"]["healthcheck"]["test"] == ["CMD", "/garage", "status"]


def test_app_storage_address_is_config():
    services = _services()
    garage_env = services["garage"]["environment"]
    pairs = {
        "S3_ACCESS_KEY": "GARAGE_DEFAULT_ACCESS_KEY",
        "S3_SECRET_KEY": "GARAGE_DEFAULT_SECRET_KEY",
        "S3_BUCKET_PHOTOS": "GARAGE_DEFAULT_BUCKET",
    }
    for name in ("api", "worker", "beat"):
        env = services[name]["environment"]
        assert env["S3_ENDPOINT_URL"] == "${S3_ENDPOINT_URL:-http://garage:3900}", name
        for app_key, garage_key in pairs.items():
            assert app_key in env, (name, app_key)
            assert env[app_key] == garage_env[garage_key], (name, app_key)


def test_app_does_not_wait_on_storage():
    depends = _services()["api"].get("depends_on", {})
    assert "minio" not in depends
    assert "garage" not in depends


def test_no_public_endpoint():
    for path in COMPOSE_FILES:
        assert "S3_PUBLIC_ENDPOINT_URL" not in path.read_text(), path.name


def test_garage_toml_has_no_secret():
    cfg = tomllib.loads((REPO / "deploy/garage/garage.toml").read_text())
    for secret in ("rpc_secret", "admin_token", "metrics_token"):
        assert secret not in cfg
    assert "admin" not in cfg
    assert cfg["db_engine"] == "sqlite"
    assert cfg["compression_level"] == "none"
    assert cfg["replication_factor"] == 1
    assert cfg["metadata_fsync"] is True
    assert cfg["data_fsync"] is True
    assert cfg["s3_api"]["s3_region"] == "us-east-1"
    assert cfg["s3_api"]["api_bind_addr"] == "[::]:3900"


def test_minio_only_when_asked_for():
    # MinIO stays defined until release 2 so the switch can read it, but nothing
    # starts it unless a command names it: fresh dev, CI and e2e stacks never pull
    # the image, which is no longer published (tracker row 41).
    assert _services()["minio"]["profiles"] == ["minio"]
    assert "profiles" not in _services()["garage"]
