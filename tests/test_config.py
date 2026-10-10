"""Configuration and startup failure tests."""

from __future__ import annotations

import pytest

from docketwise_mcp.config import ACCESS_TOKEN_ENV_VAR, ConfigError, Settings


def test_missing_access_token_names_required_environment_variable(monkeypatch) -> None:
    monkeypatch.delenv(ACCESS_TOKEN_ENV_VAR, raising=False)

    with pytest.raises(ConfigError, match=ACCESS_TOKEN_ENV_VAR):
        Settings.from_env()


def test_startup_without_credentials_runs_stdio(monkeypatch, capsys) -> None:
    from docketwise_mcp import server
    from docketwise_mcp.runtime import MCPServer

    seen = []
    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: seen.append(kwargs))
    server.main()
    assert seen == [{"transport": "stdio"}]
    assert "Traceback" not in capsys.readouterr().err


def test_settings_strip_secret_and_normalize_base_url() -> None:
    settings = Settings(access_token="  test-token  ", base_url="https://example.test/api/v1")

    assert settings.access_token == "test-token"
    assert settings.base_url == "https://example.test/api/v1/"
    assert "test-token" not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "https://[invalid",
        "https://example.invalid:bad",
        "https://example.invalid:99999",
        "https://example.invalid\uff0f",
        "not-a-url",
        "https://",
        "https:///path",
        "https://:443",
        "https://example.invalid:-1",
    ],
)
def test_malformed_base_url_fails_settings_and_verify(monkeypatch, capsys, url):
    from docketwise_mcp import setup

    monkeypatch.setenv("DOCKETWISE_ACCESS_TOKEN", "synthetic")
    monkeypatch.setenv("DOCKETWISE_BASE_URL", url)
    with pytest.raises(ConfigError, match="DOCKETWISE_BASE_URL") as exc:
        Settings.from_env()
    assert url not in str(exc.value)
    assert setup.verify() == 2
    output = capsys.readouterr()
    assert "DOCKETWISE_BASE_URL" in output.err
    assert "http" in output.err.lower()
    assert url not in output.err + output.out
    assert "Traceback" not in output.err
    assert "Local configuration is valid" not in output.out


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/api/v1",
        "https://localhost:8080/api",
        "https://[::1]:443/api",
    ],
)
def test_valid_base_url_passes_verify(monkeypatch, capsys, url):
    from docketwise_mcp import setup

    monkeypatch.setenv("DOCKETWISE_ACCESS_TOKEN", "synthetic")
    monkeypatch.setenv("DOCKETWISE_BASE_URL", url)
    assert setup.verify() == 0
    assert "Vendor authentication has not been tested" in capsys.readouterr().out
