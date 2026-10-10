"""The real DocketWise client's owned pool closes with the server lifespan."""

import httpx
import pytest
from mcp import Client

from docketwise_mcp import runtime, server
from docketwise_mcp.client import DocketWiseClient
from docketwise_mcp.config import Settings


@pytest.mark.asyncio
async def test_lazy_server_shutdown_closes_owned_http_pool(monkeypatch):
    original_http = httpx.AsyncClient
    pools = []

    def local_http(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        pool = original_http(*args, **kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(httpx, "AsyncClient", local_http)
    monkeypatch.setattr(Settings, "from_env", lambda: Settings("synthetic"))
    lazy = runtime.LazyClient()
    async with Client(server.create_server(lazy)) as session:
        assert lazy._client is None
        assert not (await session.call_tool("list_contacts", {})).is_error
        assert len(pools) == 1
        assert not pools[0].is_closed
    assert pools[0].is_closed
    assert lazy._client is None
    await lazy.aclose()  # Repeated shutdown must be harmless.


@pytest.mark.asyncio
async def test_lazy_shutdown_respects_injected_http_ownership():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    ) as pool:
        real = DocketWiseClient(Settings("synthetic"), http_client=pool)
        lazy = runtime.LazyClient()
        lazy._client = real
        await lazy.aclose()
        assert not pool.is_closed
        assert lazy._client is None
