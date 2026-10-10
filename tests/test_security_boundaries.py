"""Regression probes for credential destination validation."""

import pytest

from docketwise_mcp.config import ConfigError, Settings


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/api",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:secret@example.test/api",
        "https://example.test/api?q=secret",
        "https://example.test/api#secret",
        "https://example.test/api?",
        "https://example.test/api#",
        "https://:443",
        "https://example.test:bad",
        "https://example.test:99999",
        "https://example.test\\@evil.test",
        "https://example.test\n/path",
        "https://%65xample.test",
        "https://example.test:",
    ],
)
def test_credential_urls_reject_review_probes(url):
    with pytest.raises(ConfigError, match="HTTPS"):
        Settings(access_token="synthetic", base_url=url)
