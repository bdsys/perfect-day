"""Top-level test configuration: env vars, async event loop, shared utilities."""

from __future__ import annotations

import os

import pytest

# Provide minimal valid env so Settings() doesn't fail at import time during unit tests.
# Integration tests override these via testcontainers fixtures.
_TEST_HEX32 = "a" * 64

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("MASTER_SECRET", _TEST_HEX32)
os.environ.setdefault("OAUTH_TOKEN_SECRET", "b" * 64)
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://perfectday:perfectday@localhost:5432/perfectday_test"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql://perfectday:perfectday@localhost:5432/perfectday_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
os.environ.setdefault("ENV", "test")


# Bust the lru_cache on Settings so each test module can override env vars cleanly.
@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.core import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()
