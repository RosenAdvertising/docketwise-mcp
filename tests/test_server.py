"""MCP protocol and fully mocked tool round-trip tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.types import LATEST_PROTOCOL_VERSION

from docketwise_mcp.client import DocketWiseClient
from docketwise_mcp.config import Settings
from docketwise_mcp.server import create_server

API_BASE = "https://app.docketwise.com/api/v1/"
EXPECTED_TOOLS = {
    "list_contacts",
    "get_contact",
    "list_matters",
    "get_matter",
    "list_matter_receipts",
    "list_documents",
    "get_document_download_url",
    "list_invoices",
    "list_invoice_charges",
    "list_matter_types",
    "list_matter_statuses",
    "list_users",
    "experimental_create_contact",
    "create_note",
}


def make_server():
    client = DocketWiseClient(Settings(access_token="offline-tool-test-token"))
    return create_server(client)


@pytest.mark.anyio
async def test_server_negotiates_required_2026_07_28_protocol_revision() -> None:
    assert LATEST_PROTOCOL_VERSION == "2026-07-28"
    async with Client(make_server()) as mcp_client:
        assert str(mcp_client.protocol_version) == "2026-07-28"


@pytest.mark.anyio
async def test_tool_inventory_has_plain_descriptions_and_described_parameters() -> None:
    async with Client(make_server()) as mcp_client:
        listing = await mcp_client.list_tools()

    assert {tool.name for tool in listing.tools} == EXPECTED_TOOLS
    for tool in listing.tools:
        assert tool.description
        assert "\n" not in tool.description
        for parameter in tool.input_schema.get("properties", {}).values():
            assert parameter.get("description"), f"{tool.name} has an undescribed parameter"


READ_TOOL_CASES = [
    (
        "list_contacts",
        {"contact_type": "Person", "filter": "all", "page": 1, "limit": 200},
        "contacts",
        {"type": "Person", "filter": "all", "page": 1},
        [{"id": 1}],
        "items",
    ),
    ("get_contact", {"contact_id": 7}, "contacts/7", None, {"id": 7}, "contact"),
    ("list_matters", {"page": 1, "limit": 200}, "matters", {"page": 1}, [{"id": 2}], "items"),
    ("get_matter", {"matter_id": 2}, "matters/2", None, {"id": 2}, "matter"),
    (
        "list_matter_receipts",
        {"matter_id": 2, "limit": 200},
        "matters/2/receipts",
        None,
        [{"id": 3}],
        "items",
    ),
    (
        "list_documents",
        {"search": "visa", "page": 1, "limit": 200},
        "documents",
        {"search": "visa", "page": 1},
        [{"id": 4}],
        "items",
    ),
    (
        "get_document_download_url",
        {"document_id": 4},
        "documents/4/download",
        None,
        {"url": "https://download.example.test/one-hour"},
        "download",
    ),
    ("list_invoices", {"page": 1, "limit": 200}, "invoices", {"page": 1}, [{"id": 5}], "items"),
    (
        "list_invoice_charges",
        {"invoice_id": 5, "page": 1, "limit": 200},
        "invoices/5/charges",
        {"page": 1},
        [{"id": 6}],
        "items",
    ),
    (
        "list_matter_types",
        {"page": 1, "limit": 200},
        "matter_types",
        {"page": 1},
        [{"id": 8}],
        "items",
    ),
    (
        "list_matter_statuses",
        {"page": 1, "limit": 200},
        "matter_statuses",
        {"page": 1},
        [{"id": 9}],
        "items",
    ),
    ("list_users", {"page": 1, "limit": 200}, "users", {"page": 1}, [{"id": 10}], "items"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "path", "params", "payload", "result_key"),
    READ_TOOL_CASES,
)
async def test_each_read_tool_makes_its_documented_round_trip(
    tool_name: str,
    arguments: dict[str, Any],
    path: str,
    params: dict[str, Any] | None,
    payload: Any,
    result_key: str,
) -> None:
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = (
            router.get(f"{API_BASE}{path}", params=params)
            if params
            else router.get(f"{API_BASE}{path}")
        )
        route.respond(200, json=payload)
        async with Client(make_server()) as mcp_client:
            result = await mcp_client.call_tool(tool_name, arguments)

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content[result_key] == payload


@pytest.mark.anyio
async def test_experimental_create_contact_posts_documented_wrapper() -> None:
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.post(f"{API_BASE}contacts").respond(201, json={"id": 11})
        async with Client(make_server()) as mcp_client:
            result = await mcp_client.call_tool(
                "experimental_create_contact",
                {
                    "contact_type": "Person",
                    "confirm": True,
                    "first_name": "Example",
                    "last_name": "Person",
                    "lead": True,
                    "addresses": [{"city": "London", "physical": True}],
                    "phone_numbers": [{"number": "+44 20 0000 0000", "daytime": True}],
                },
            )

    assert result.is_error is False
    assert result.structured_content == {"contact": {"id": 11}}
    request_body = route.calls[0].request.content
    assert httpx.Response(200, content=request_body).json() == {
        "contact": {
            "first_name": "Example",
            "last_name": "Person",
            "type": "Person",
            "lead": True,
            "addresses_attributes": [{"data": {"city": "London", "physical": True}}],
            "phone_numbers_attributes": [{"data": {"number": "+44 20 0000 0000", "daytime": True}}],
        }
    }


@pytest.mark.anyio
async def test_create_note_posts_flat_documented_body() -> None:
    response_payload = {"id": 12, "title": "Called client"}
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.post(f"{API_BASE}notes").respond(201, json=response_payload)
        async with Client(make_server()) as mcp_client:
            result = await mcp_client.call_tool(
                "create_note",
                {
                    "matter_id": 2,
                    "title": "Called client",
                    "content": "Left a voicemail.",
                    "confirm": True,
                },
            )

    assert result.is_error is False
    assert result.structured_content == {"note": response_payload}
    assert httpx.Response(200, content=route.calls[0].request.content).json() == {
        "matter_id": 2,
        "title": "Called client",
        "content": "Left a voicemail.",
    }


@pytest.mark.anyio
async def test_write_tool_rejects_missing_confirmation_without_http() -> None:
    with respx.mock(assert_all_mocked=True) as router:
        async with Client(make_server()) as mcp_client:
            result = await mcp_client.call_tool(
                "create_note",
                {
                    "client_id": 3,
                    "title": "Should not write",
                    "content": "No confirmation was given.",
                    "confirm": False,
                },
            )

    assert result.is_error is True
    assert "Confirmation required" in result.content[0].text
    assert not router.calls.called


@pytest.mark.anyio
async def test_tool_error_redacts_vendor_message_from_result_and_logs(caplog) -> None:
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        router.get(f"{API_BASE}contacts/999").respond(
            404, json={"error": "Contact could not be found."}
        )
        async with Client(make_server()) as mcp_client:
            result = await mcp_client.call_tool("get_contact", {"contact_id": 999})

    assert result.is_error is True
    assert "Resource not found. Check the record ID and account access." in result.content[0].text
    assert "Contact could not be found." not in result.content[0].text
    assert "Contact could not be found." not in caplog.text
