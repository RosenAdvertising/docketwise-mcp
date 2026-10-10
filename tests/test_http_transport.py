"""Offline transport, security, and concurrent lifespan regressions."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import respx
from mcp import Client
from test_spec_2026_07_28 import (
    API_BASE,
    PROTOCOL_VERSION,
    SERVER_INFO_META_KEY,
    _modern_request,
    _result,
)

from docketwise_mcp import __version__, runtime, server


@pytest.fixture(autouse=True)
def clean_http_environment(monkeypatch):
    for name in (
        "DOCKETWISE_MCP_TRANSPORT",
        "DOCKETWISE_MCP_HOST",
        "PORT",
        "DOCKETWISE_MCP_ALLOWED_HOSTS",
        "DOCKETWISE_MCP_ALLOWED_ORIGINS",
        "DOCKETWISE_ACCESS_TOKEN",
        "DOCKETWISE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
async def http_client():
    app = server.create_serve_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        yield client


async def post(client, method, params=None, *, extra_headers=None):
    headers, body = _modern_request(method, params)
    headers.update(extra_headers or {})
    return await client.post("/mcp", headers=headers, json=body)


@pytest.mark.anyio
async def test_http_tools_match_stdio_inventory(http_client):
    result = _result(await post(http_client, "tools/list"))
    async with Client(server.create_server(), cache=None) as stdio:
        listing = await stdio.list_tools()
    assert {tool["name"]: tool["inputSchema"] for tool in result["tools"]} == {
        tool.name: tool.input_schema for tool in listing.tools
    }
    assert len(result["tools"]) == 14


@pytest.mark.anyio
async def test_read_tool_uses_environment_credentials(http_client, monkeypatch):
    monkeypatch.setenv("DOCKETWISE_ACCESS_TOKEN", "offline-tool-test-token")
    monkeypatch.setenv("DOCKETWISE_BASE_URL", API_BASE)
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.get(f"{API_BASE}contacts/7").respond(
            200, json={"id": 7, "first_name": "Ada"}
        )
        result = _result(
            await post(
                http_client,
                "tools/call",
                {"name": "get_contact", "arguments": {"contact_id": 7}},
                extra_headers={"authorization": "Bearer request-must-not-be-used"},
            )
        )
    assert result["isError"] is False
    assert result["structuredContent"] == {"contact": {"id": 7, "first_name": "Ada"}}
    assert route.calls[0].request.headers["authorization"] == "Bearer offline-tool-test-token"


@pytest.mark.anyio
async def test_two_requests_need_no_session_or_initialization(http_client):
    first = await post(http_client, "tools/list", extra_headers={"mcp-session-id": "ignored-first"})
    second = await post(
        http_client, "tools/list", extra_headers={"mcp-session-id": "ignored-second"}
    )
    assert _result(first) == _result(second)
    assert "mcp-session-id" not in first.headers
    assert "mcp-session-id" not in second.headers


@pytest.mark.anyio
async def test_discovery_and_post_only_endpoint(http_client):
    result = _result(await post(http_client, "server/discover"))
    assert PROTOCOL_VERSION in result["supportedVersions"]
    info = result["_meta"][SERVER_INFO_META_KEY]
    assert all(info[field] for field in ("name", "title", "version"))
    for method in ("GET", "DELETE"):
        response = await http_client.request(
            method, "/mcp", headers={"mcp-protocol-version": PROTOCOL_VERSION}
        )
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"


def test_default_transport_keeps_existing_stdio_call(monkeypatch):
    calls = []

    class StdioServer:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(server, "create_server", StdioServer)
    assert server._requested_transport() == "stdio"
    server.main()
    assert calls == [{"transport": "stdio"}]


@pytest.mark.parametrize("value", [" STDIO ", "", "   "])
def test_transport_normalization(value, monkeypatch):
    monkeypatch.setenv("DOCKETWISE_MCP_TRANSPORT", value)
    assert server._requested_transport() == "stdio"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_transport_variable_starts_stdio(value, monkeypatch):
    """A set-but-empty or whitespace transport keeps stdio startup (F3)."""

    calls = []

    class StdioServer:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(server, "create_server", StdioServer)
    monkeypatch.setenv("DOCKETWISE_MCP_TRANSPORT", value)
    server.main()
    assert calls == [{"transport": "stdio"}]


def test_invalid_transport_exits_with_choices(monkeypatch):
    monkeypatch.setenv("DOCKETWISE_MCP_TRANSPORT", "bogus")
    with pytest.raises(SystemExit, match="DOCKETWISE_MCP_TRANSPORT.*stdio.*streamable-http"):
        server.main()


def test_http_transport_dispatch(monkeypatch):
    calls = []

    async def serve():
        calls.append("served")

    monkeypatch.setenv("DOCKETWISE_MCP_TRANSPORT", " STREAMABLE-HTTP ")
    monkeypatch.setattr(server, "_serve_streamable_http", serve)
    server.main()
    assert calls == ["served"]


def test_host_and_port_defaults_and_overrides(monkeypatch):
    assert server._host() == "127.0.0.1"
    assert server._port() == 8080
    monkeypatch.setenv("DOCKETWISE_MCP_HOST", " 0.0.0.0 ")
    monkeypatch.setenv("PORT", " 9000 ")
    assert server._host() == "0.0.0.0"
    assert server._port() == 9000
    monkeypatch.setenv("PORT", "invalid")
    with pytest.raises(SystemExit, match="PORT must be an integer"):
        server._port()


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_host_variable_yields_loopback(value, monkeypatch):
    """A set-but-empty or whitespace host defaults to 127.0.0.1, never an empty bind (F4)."""

    monkeypatch.setenv("DOCKETWISE_MCP_HOST", value)
    assert server._host() == "127.0.0.1"
    assert server._transport_security() is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_uses_sdk_security(host, monkeypatch):
    monkeypatch.setenv("DOCKETWISE_MCP_HOST", host)
    assert server._transport_security() is None


def test_uppercase_localhost_is_not_loopback(monkeypatch):
    """_host() keeps the caller's spelling, so LOCALHOST exits without allowed hosts (F7)."""

    monkeypatch.setenv("DOCKETWISE_MCP_HOST", "LOCALHOST")
    assert server._host() == "LOCALHOST"
    with pytest.raises(SystemExit, match="DOCKETWISE_MCP_ALLOWED_HOSTS"):
        server.create_serve_app()


def test_server_imports_without_installed_distribution(monkeypatch):
    """Importing the server survives a missing distribution metadata read (F8)."""

    def raise_not_found(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version("docketwise-mcp")
    spec = importlib.util.spec_from_file_location(
        "docketwise_mcp_server_without_distribution", Path(server.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__version__ == __version__


@pytest.mark.parametrize("allowed_hosts", [None, "", " , , "])
def test_nonloopback_requires_allowed_hosts(allowed_hosts, monkeypatch):
    monkeypatch.setenv("DOCKETWISE_MCP_HOST", "0.0.0.0")
    if allowed_hosts is not None:
        monkeypatch.setenv("DOCKETWISE_MCP_ALLOWED_HOSTS", allowed_hosts)
    with pytest.raises(SystemExit, match="DOCKETWISE_MCP_ALLOWED_HOSTS"):
        server.create_serve_app()


@pytest.mark.anyio
async def test_host_and_origin_allowlists(monkeypatch):
    monkeypatch.setenv("DOCKETWISE_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("DOCKETWISE_MCP_ALLOWED_HOSTS", " mcp.example.test, mcp.example.test:* , ")
    monkeypatch.setenv("DOCKETWISE_MCP_ALLOWED_ORIGINS", " https://client.example.test, ")
    settings = server._transport_security()
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["mcp.example.test", "mcp.example.test:*"]
    assert settings.allowed_origins == ["https://client.example.test"]
    app = server.create_serve_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://mcp.example.test"
        ) as client,
    ):
        assert (
            await post(client, "tools/list", extra_headers={"host": "other.example.test"})
        ).status_code == 421
        assert (
            await post(client, "tools/list", extra_headers={"origin": "https://bad.example.test"})
        ).status_code == 403
        assert (
            await post(
                client, "tools/list", extra_headers={"origin": "https://client.example.test"}
            )
        ).status_code == 200
        assert (await post(client, "tools/list")).status_code == 200


@pytest.mark.anyio
async def test_loopback_rejects_bad_origin(http_client):
    response = await post(
        http_client, "tools/list", extra_headers={"origin": "https://bad.example.test"}
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_lifespan_runs_once_and_concurrent_requests_share_safe_pool(monkeypatch):
    monkeypatch.setenv("DOCKETWISE_ACCESS_TOKEN", "offline-tool-test-token")
    monkeypatch.setenv("DOCKETWISE_BASE_URL", API_BASE)
    events = []

    class TrackingLazyClient(runtime.LazyClient):
        async def __aenter__(self):
            events.append("enter")
            return await super().__aenter__()

        async def __aexit__(self, *args):
            events.append("exit")
            await super().__aexit__(*args)

    lazy = TrackingLazyClient()
    mcp = server.create_server(lazy)
    monkeypatch.setattr(server, "create_server", lambda: mcp)
    app = server.create_serve_app()
    reached_vendor = asyncio.Event()
    contexts = []
    requests = []
    original_call_tool = mcp.call_tool

    async def call_tool(name, arguments, context=None):
        contexts.append(context.request_context)
        return await original_call_tool(name, arguments, context)

    monkeypatch.setattr(mcp, "call_tool", call_tool)

    async def respond(request):
        requests.append(request)
        if len(requests) == 2:
            reached_vendor.set()
        await asyncio.wait_for(reached_vendor.wait(), timeout=5)
        return httpx.Response(200, json={"id": int(request.url.path.rsplit("/", 1)[1])})

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        router.get(f"{API_BASE}contacts/7").mock(side_effect=respond)
        router.get(f"{API_BASE}contacts/8").mock(side_effect=respond)
        async with app.router.lifespan_context(app):
            assert events == ["enter"]
            assert lazy._client is None
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
            ) as client:
                responses = await asyncio.gather(
                    *[
                        post(
                            client,
                            "tools/call",
                            {"name": "get_contact", "arguments": {"contact_id": record_id}},
                        )
                        for record_id in (7, 8)
                    ]
                )
            assert [_result(response)["structuredContent"] for response in responses] == [
                {"contact": {"id": 7}},
                {"contact": {"id": 8}},
            ]
            assert all("mcp-session-id" not in response.headers for response in responses)
            assert contexts[0] is not contexts[1]
            assert contexts[0].session is not contexts[1].session
            assert events == ["enter"]
            pool = lazy._client._http_client
            assert not pool.is_closed
    assert events == ["enter", "exit"]
    assert pool.is_closed
    assert lazy._client is None


@pytest.mark.anyio
async def test_disconnect_cancels_vendor_tool(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingClient(runtime.LazyClient):
        async def get(self, _path):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    mcp = server.create_server(WaitingClient())
    monkeypatch.setattr(server, "create_server", lambda: mcp)
    app = server.create_serve_app()
    headers, body = _modern_request(
        "tools/call", {"name": "get_contact", "arguments": {"contact_id": 7}}
    )
    headers["host"] = "127.0.0.1:8080"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "server": ("127.0.0.1", 8080),
        "client": ("127.0.0.1", 12345),
        "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
    }
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await started.wait()
        return {"type": "http.disconnect"}

    async def send(_message):
        pass

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        assert started.is_set()
        assert cancelled.is_set()
