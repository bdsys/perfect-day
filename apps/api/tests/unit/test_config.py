"""Unit tests: Settings hex32 validator and CORS origins parsing."""

from __future__ import annotations

import pytest


class TestHex32Validator:
    def test_valid_64_char_hex_accepted(self, monkeypatch):
        monkeypatch.setenv("MASTER_SECRET", "a" * 64)
        monkeypatch.setenv("OAUTH_TOKEN_SECRET", "b" * 64)
        from app.core.config import Settings

        s = Settings()
        assert s.master_secret == "a" * 64
        assert s.oauth_token_secret == "b" * 64

    def test_too_short_rejected(self, monkeypatch):
        from pydantic import ValidationError

        monkeypatch.setenv("MASTER_SECRET", "a" * 62)
        monkeypatch.setenv("OAUTH_TOKEN_SECRET", "b" * 64)
        with pytest.raises(ValidationError, match="32 bytes"):
            from app.core.config import Settings

            Settings()

    def test_too_long_rejected(self, monkeypatch):
        from pydantic import ValidationError

        monkeypatch.setenv("MASTER_SECRET", "a" * 66)
        monkeypatch.setenv("OAUTH_TOKEN_SECRET", "b" * 64)
        with pytest.raises(ValidationError, match="32 bytes"):
            from app.core.config import Settings

            Settings()

    def test_non_hex_rejected(self, monkeypatch):
        from pydantic import ValidationError

        monkeypatch.setenv("MASTER_SECRET", "z" * 64)
        monkeypatch.setenv("OAUTH_TOKEN_SECRET", "b" * 64)
        with pytest.raises(ValidationError, match="hex"):
            from app.core.config import Settings

            Settings()

    def test_uppercase_hex_accepted(self, monkeypatch):
        monkeypatch.setenv("MASTER_SECRET", "A" * 64)
        monkeypatch.setenv("OAUTH_TOKEN_SECRET", "B" * 64)
        from app.core.config import Settings

        s = Settings()
        assert len(bytes.fromhex(s.master_secret)) == 32


class TestCorsOrigins:
    def test_json_list_parsed(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3000","https://example.com"]')
        from app.core.config import Settings

        s = Settings()
        assert "http://localhost:3000" in s.cors_origins
        assert "https://example.com" in s.cors_origins

    def test_default_includes_localhost(self):
        from app.core.config import Settings

        s = Settings()
        assert "http://localhost:3000" in s.cors_origins


def test_settings_have_open_meteo_defaults():
    from app.core.config import get_settings
    s = get_settings()
    assert s.weather_enabled is True
    assert s.open_meteo_forecast_url == "https://api.open-meteo.com/v1/forecast"
    assert s.open_meteo_archive_url == "https://archive-api.open-meteo.com/v1/archive"
    assert s.open_meteo_timeout_seconds == 30
