"""Environment-only configuration for the DocketWise MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from docketwise_mcp.urls import require_https

DEFAULT_BASE_URL = "https://app.docketwise.com/api/v1/"
ACCESS_TOKEN_ENV_VAR = "DOCKETWISE_ACCESS_TOKEN"
BASE_URL_ENV_VAR = "DOCKETWISE_BASE_URL"


class ConfigError(RuntimeError):
    """Report an actionable configuration problem without exposing secrets."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded exclusively from environment variables."""

    access_token: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL

    def __post_init__(self) -> None:
        token = self.access_token.strip()
        if not token:
            raise ConfigError(
                f"Missing required environment variable {ACCESS_TOKEN_ENV_VAR}. "
                "Set it to a DocketWise OAuth access token."
            )
        object.__setattr__(self, "access_token", token)

        base_url = self.base_url.strip()
        try:
            require_https(base_url, BASE_URL_ENV_VAR)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        object.__setattr__(self, "base_url", f"{base_url.rstrip('/')}/")

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from the process environment."""

        token = os.environ.get(ACCESS_TOKEN_ENV_VAR, "")
        if not token.strip():
            raise ConfigError(
                f"Missing required environment variable {ACCESS_TOKEN_ENV_VAR}. "
                "Set it to a DocketWise OAuth access token."
            )
        return cls(
            access_token=token,
            base_url=os.environ.get(BASE_URL_ENV_VAR, DEFAULT_BASE_URL),
        )
