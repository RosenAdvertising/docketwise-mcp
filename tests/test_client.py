"""Fully mocked tests for the DocketWise HTTP client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from docketwise_mcp.client import DocketWiseAPIError, DocketWiseClient
from docketwise_mcp.config import Settings

API_BASE = "https://app.docketwise.com/api/v1/"


def make_client(**kwargs: Any) -> DocketWiseClient:
    return DocketWiseClient(Settings(access_token="offline-test-token"), **kwargs)


@pytest.mark.anyio
async def test_happy_path_sends_bearer_auth() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer offline-test-token"
        return httpx.Response(200, json={"id": 7, "first_name": "Ada"})

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        router.get(f"{API_BASE}contacts/7").mock(side_effect=respond)
        async with make_client() as client:
            result = await client.get("contacts/7")

    assert result == {"id": 7, "first_name": "Ada"}


@pytest.mark.anyio
async def test_auth_failure_surfaces_vendor_message() -> None:
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        router.get(f"{API_BASE}contacts/7").respond(
            401, json={"message": "The access token is invalid."}
        )
        async with make_client() as client:
            with pytest.raises(DocketWiseAPIError) as exc_info:
                await client.get("contacts/7")

    assert exc_info.value.status_code == 401
    assert "The access token is invalid." in str(exc_info.value)


@pytest.mark.anyio
async def test_429_retries_using_vendor_retry_header() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.get(f"{API_BASE}matters")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-Header": "1.25"}, json={"error": "Slow down"}),
            httpx.Response(200, json=[{"id": 1}]),
        ]
        async with make_client(sleep=fake_sleep) as client:
            result = await client.get("matters")

    assert result == [{"id": 1}]
    assert route.call_count == 2
    assert delays == [1.25]


@pytest.mark.anyio
async def test_paginate_fetches_successive_200_record_pages() -> None:
    first_page = [{"id": value} for value in range(1, 201)]
    second_page = [{"id": 201}, {"id": 202}]

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        router.get(f"{API_BASE}contacts", params={"type": "Person", "page": 3}).respond(
            200, json=first_page
        )
        router.get(f"{API_BASE}contacts", params={"type": "Person", "page": 4}).respond(
            200, json=second_page
        )
        async with make_client() as client:
            result = await client.paginate("contacts", page=3, limit=250, params={"type": "Person"})

    assert len(result["items"]) == 202
    assert result["items"][-1] == {"id": 202}
    assert result["pagination"] == {
        "start_page": 3,
        "pages_fetched": 2,
        "returned": 202,
        "limit": 250,
        "has_more": False,
        "next_page": None,
        "truncated_within_page": False,
    }


@pytest.mark.anyio
async def test_5xx_retries_with_exponential_backoff() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.get(f"{API_BASE}users")
        route.side_effect = [
            httpx.Response(503, text="Temporarily unavailable"),
            httpx.Response(200, json=[]),
        ]
        async with make_client(sleep=fake_sleep, backoff_seconds=0.25) as client:
            result = await client.get("users")

    assert result == []
    assert delays == [0.25]
