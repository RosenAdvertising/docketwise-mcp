"""Offline raw-wire regressions adapted from the supplied Actionstep reference."""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.types import LATEST_PROTOCOL_VERSION
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from docketwise_mcp import __version__, server

PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
API_BASE = "https://app.docketwise.com/api/v1/"


def _modern_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    request_id: int = 1,
) -> tuple[dict[str, str], dict[str, Any]]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: protocol_version,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {"name": "docketwise-spec-test", "version": "0"},
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": protocol_version,
        "mcp-method": method,
    }
    if method in {"tools/call", "prompts/get"}:
        headers["mcp-name"] = str(request_params["name"])
    elif method == "resources/read":
        headers["mcp-name"] = str(request_params["uri"])
    return headers, {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def _result(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        payload = json.loads(
            next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
        )
    else:
        payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    return payload["result"]


async def _post_modern(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    header_overrides: dict[str, str] | None = None,
) -> httpx.Response:
    app = server.create_server().streamable_http_app(stateless_http=True, json_response=True)
    headers, body = _modern_request(method, params, protocol_version=protocol_version)
    headers.update(header_overrides or {})
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        return await client.post("/mcp", headers=headers, json=body)


def test_sdk_pins_the_2026_revision() -> None:
    assert version("mcp") == "2.3.0"
    assert LATEST_PROTOCOL_VERSION == PROTOCOL_VERSION
    assert MODERN_PROTOCOL_VERSIONS == (PROTOCOL_VERSION,)


@pytest.mark.anyio
async def test_modern_discovery_is_sessionless_and_declares_used_capabilities() -> None:
    response = await _post_modern("server/discover")
    result = _result(response)
    assert "mcp-session-id" not in response.headers
    assert result["supportedVersions"] == [PROTOCOL_VERSION]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert result["capabilities"] == {
        "prompts": {"listChanged": True},
        "resources": {"listChanged": True, "subscribe": True},
        "tools": {"listChanged": True},
    }
    assert "extensions" not in result["capabilities"]
    info = result["_meta"][SERVER_INFO_META_KEY]
    assert info["name"] == "docketwise"
    assert info["title"] == "DocketWise"
    assert info["version"] == __version__


@pytest.mark.anyio
async def test_client_negotiates_modern_and_keeps_legacy_compatibility() -> None:
    async with Client(server.create_server(), cache=None) as modern:
        assert modern.protocol_version == PROTOCOL_VERSION
    async with Client(server.create_server(), mode="legacy", cache=None) as legacy:
        assert legacy.protocol_version == "2025-11-25"


@pytest.mark.anyio
async def test_cacheable_results_are_complete_private_and_deterministic() -> None:
    methods = (
        "tools/list",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
    )
    results = [_result(await _post_modern(method)) for method in methods]
    for result in results:
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
    first, second, prompts, resources, templates = results
    assert first["tools"] == second["tools"]
    assert len(first["tools"]) == 14
    assert all(tool["inputSchema"]["type"] == "object" for tool in first["tools"])
    assert prompts["prompts"] == []
    assert resources["resources"] == []
    assert templates["resourceTemplates"] == []


@pytest.mark.anyio
async def test_missing_resource_is_invalid_params() -> None:
    missing = await _post_modern("resources/read", {"uri": "docketwise://does-not-exist"})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == -32602


@pytest.mark.anyio
async def test_modern_tool_results_are_structured_and_complete(monkeypatch) -> None:
    monkeypatch.setenv("DOCKETWISE_ACCESS_TOKEN", "offline-tool-test-token")
    monkeypatch.setenv("DOCKETWISE_BASE_URL", API_BASE)
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        router.get(f"{API_BASE}contacts/7").respond(200, json={"id": 7})
        response = await _post_modern(
            "tools/call", {"name": "get_contact", "arguments": {"contact_id": 7}}
        )
    result = _result(response)
    assert result["isError"] is False
    assert result["resultType"] == "complete"
    assert result["structuredContent"] == {"contact": {"id": 7}}
    assert json.loads(result["content"][0]["text"]) == {"contact": {"id": 7}}


@pytest.mark.anyio
async def test_modern_http_enforces_routing_headers_versions_and_method_errors() -> None:
    mismatch = await _post_modern("tools/list", header_overrides={"mcp-method": "resources/list"})
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == -32020
    named_mismatch = await _post_modern(
        "tools/call",
        {"name": "get_contact", "arguments": {"contact_id": 7}},
        header_overrides={"mcp-name": "get_matter"},
    )
    assert named_mismatch.status_code == 400
    assert named_mismatch.json()["error"]["code"] == -32020
    unsupported = await _post_modern("tools/list", protocol_version="2099-01-01")
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {"supported": [PROTOCOL_VERSION], "requested": "2099-01-01"},
    }
    unknown = await _post_modern("example/unknown")
    assert unknown.status_code == 404
    assert unknown.json()["error"] == {
        "code": -32601,
        "message": "Method not found",
        "data": "example/unknown",
    }


@pytest.mark.anyio
async def test_unknown_tool_stays_an_error_result() -> None:
    result = _result(await _post_modern("tools/call", {"name": "does_not_exist", "arguments": {}}))
    assert result["isError"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["mcp-method", "mcp-name"])
async def test_missing_routing_header_is_rejected(missing) -> None:
    response = await _post_modern(
        "tools/call",
        {"name": "get_contact", "arguments": {"contact_id": 7}},
        header_overrides={missing: ""},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
