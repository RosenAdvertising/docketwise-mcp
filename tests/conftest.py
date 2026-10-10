"""Shared test configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on the stdlib asyncio backend only."""

    return "asyncio"
