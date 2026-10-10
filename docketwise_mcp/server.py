"""MCP application, transports, and DocketWise tool registrations."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette

from docketwise_mcp import __version__
from docketwise_mcp.client import (
    MAX_TOOL_LIMIT,
    VENDOR_PAGE_SIZE,
    DocketWiseClient,
)
from docketwise_mcp.paths import path_id
from docketwise_mcp.runtime import LazyClient
from docketwise_mcp.runtime import SafeMCPServer as MCPServer

STREAMABLE_HTTP_TRANSPORT = "streamable-http"

PositiveId = Annotated[int, Field(gt=0, description="The positive DocketWise record ID.")]
Page = Annotated[int, Field(ge=1, description="The first DocketWise page to read.")]
Limit = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_TOOL_LIMIT,
        description="The most records to return across pages, from 1 to 1000.",
    ),
]
Confirmation = Annotated[
    bool,
    Field(description="Set this to true to confirm that DocketWise may be changed."),
]


class ContactAddress(BaseModel):
    """A physical or mailing address for a new contact."""

    model_config = ConfigDict(extra="forbid")

    street_number_and_name: str | None = Field(
        default=None, description="The building number and street name."
    )
    apartment_number: str | None = Field(
        default=None, description="The apartment, suite, or unit number."
    )
    city: str | None = Field(default=None, description="The city name.")
    state: str | None = Field(default=None, description="The US state name or abbreviation.")
    province: str | None = Field(default=None, description="The province or region name.")
    zip_code: str | None = Field(default=None, description="The ZIP or postal code.")
    physical: bool | None = Field(
        default=None, description="Whether this is the contact's physical address."
    )
    mailing: bool | None = Field(
        default=None, description="Whether this is the contact's mailing address."
    )


class ContactPhoneNumber(BaseModel):
    """A phone number for a new contact."""

    model_config = ConfigDict(extra="forbid")

    number: str = Field(description="The contact's phone number.")
    daytime: bool | None = Field(
        default=None, description="Whether this is the preferred daytime number."
    )


def _require_confirmation(confirm: bool, action: str) -> None:
    if confirm is not True:
        raise ToolError(f"Confirmation required: set confirm=true to {action}.")


def _optional_fields(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def create_server(client: DocketWiseClient | LazyClient | None = None) -> MCPServer:
    """Create the configured MCP application around a DocketWise client."""

    client = client if client is not None else LazyClient()

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        async with client:
            yield None

    app = MCPServer(
        name="docketwise",
        title="DocketWise",
        description="Read and make explicitly confirmed changes in DocketWise.",
        version=__version__,
        lifespan=lifespan,
    )

    @app.tool(structured_output=True)
    async def list_contacts(
        contact_type: Annotated[
            Literal["Person", "Institution"] | None,
            Field(description="Optionally return only people or only institutions."),
        ] = None,
        filter: Annotated[
            Literal["all"] | None,
            Field(description="Optionally use the documented all-contacts filter."),
        ] = None,
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the firm's contacts, optionally narrowing them by contact kind."""

        params = _optional_fields(type=contact_type, filter=filter)
        return await client.paginate("contacts", page=page, limit=limit, params=params)

    @app.tool(structured_output=True)
    async def get_contact(contact_id: PositiveId) -> dict[str, Any]:
        """Get one contact together with their address and notes."""

        return {"contact": await client.get(f"contacts/{path_id(contact_id, 'contact_id')}")}

    @app.tool(structured_output=True)
    async def list_matters(
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the firm's immigration matters."""

        return await client.paginate("matters", page=page, limit=limit)

    @app.tool(structured_output=True)
    async def get_matter(matter_id: PositiveId) -> dict[str, Any]:
        """Get one matter with its client, receipts, assigned staff, and notes."""

        return {"matter": await client.get(f"matters/{path_id(matter_id, 'matter_id')}")}

    @app.tool(structured_output=True)
    async def list_matter_receipts(
        matter_id: PositiveId,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the USCIS receipt records attached to one matter."""

        payload = await client.get(f"matters/{path_id(matter_id, 'matter_id')}/receipts")
        if not isinstance(payload, list):
            raise ValueError("DocketWise returned a non-list response for matter receipts.")
        return {
            "items": payload[:limit],
            "pagination": {
                "returned": min(len(payload), limit),
                "limit": limit,
                "has_more": len(payload) > limit,
            },
        }

    @app.tool(structured_output=True)
    async def list_documents(
        search: Annotated[
            str | None,
            Field(max_length=500, description="Optional text to match against document titles."),
        ] = None,
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the firm's documents, optionally searching their titles."""

        params = _optional_fields(search=search)
        return await client.paginate("documents", page=page, limit=limit, params=params)

    @app.tool(structured_output=True)
    async def get_document_download_url(document_id: PositiveId) -> dict[str, Any]:
        """Create a public download link for a document that lasts one hour."""

        payload = await client.get(f"documents/{path_id(document_id, 'document_id')}/download")
        return {"document_id": document_id, "download": payload}

    @app.tool(structured_output=True)
    async def list_invoices(
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the firm's invoices and their payment balances."""

        return await client.paginate("invoices", page=page, limit=limit)

    @app.tool(structured_output=True)
    async def list_invoice_charges(
        invoice_id: Annotated[int, Field(gt=0, description="The positive DocketWise invoice ID.")],
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the service, expense, and fee items on one invoice."""

        return await client.paginate(
            f"invoices/{path_id(invoice_id, 'invoice_id')}/charges", page=page, limit=limit
        )

    @app.tool(structured_output=True)
    async def list_matter_types(
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the firm's matter types and their workflow statuses."""

        return await client.paginate("matter_types", page=page, limit=limit)

    @app.tool(structured_output=True)
    async def list_matter_statuses(
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List the workflow statuses the firm uses for matters."""

        return await client.paginate("matter_statuses", page=page, limit=limit)

    @app.tool(structured_output=True)
    async def list_users(
        page: Page = 1,
        limit: Limit = VENDOR_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List firm users so attorneys and assignees can be identified."""

        return await client.paginate("users", page=page, limit=limit)

    @app.tool(structured_output=True)
    async def experimental_create_contact(
        *,
        contact_type: Annotated[
            Literal["Person", "Institution"],
            Field(description="Whether the new contact is a person or institution."),
        ],
        confirm: Confirmation,
        first_name: Annotated[
            str | None, Field(description="The contact's first or given name.")
        ] = None,
        middle_name: Annotated[str | None, Field(description="The contact's middle name.")] = None,
        last_name: Annotated[
            str | None, Field(description="The contact's last, family, or institution name.")
        ] = None,
        email: Annotated[str | None, Field(description="The contact's email address.")] = None,
        lead: Annotated[
            bool | None, Field(description="Whether the contact should be marked as a lead.")
        ] = None,
        addresses: Annotated[
            list[ContactAddress] | None,
            Field(description="Optional physical or mailing addresses for the contact."),
        ] = None,
        phone_numbers: Annotated[
            list[ContactPhoneNumber] | None,
            Field(description="Optional phone numbers for the contact."),
        ] = None,
    ) -> dict[str, Any]:
        """Create a contact after you explicitly confirm the change."""

        _require_confirmation(confirm, "create this contact")
        contact = _optional_fields(
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            email=email,
            type=contact_type,
            lead=lead,
        )
        if addresses is not None:
            contact["addresses_attributes"] = [
                {"data": address.model_dump(exclude_none=True)} for address in addresses
            ]
        if phone_numbers is not None:
            contact["phone_numbers_attributes"] = [
                {"data": phone.model_dump(exclude_none=True)} for phone in phone_numbers
            ]
        payload = await client.post("contacts", json_body={"contact": contact})
        return {"contact": payload}

    @app.tool(structured_output=True)
    async def create_note(
        *,
        title: Annotated[str, Field(min_length=1, description="A short title for the note.")],
        content: Annotated[str, Field(min_length=1, description="The note text to save.")],
        confirm: Confirmation,
        matter_id: Annotated[
            int | None,
            Field(gt=0, description="The matter to attach the note to, if applicable."),
        ] = None,
        client_id: Annotated[
            int | None,
            Field(gt=0, description="The client to attach the note to, if applicable."),
        ] = None,
    ) -> dict[str, Any]:
        """Add a note to a matter or client after you explicitly confirm it."""

        _require_confirmation(confirm, "create this note")
        if matter_id is None and client_id is None:
            raise ToolError("A matter_id or client_id is required to create a note.")
        note = _optional_fields(
            matter_id=matter_id,
            client_id=client_id,
            title=title,
            content=content,
        )
        return {"note": await client.post("notes", json_body=note)}

    return app


def _requested_transport() -> str:
    return os.environ.get("DOCKETWISE_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"


def _host() -> str:
    return os.environ.get("DOCKETWISE_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _port() -> int:
    raw = os.environ.get("PORT", "8080").strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"PORT must be an integer, got {raw!r}.") from exc


def _transport_security() -> TransportSecuritySettings | None:
    if _host() in {"127.0.0.1", "localhost", "::1"}:
        return None  # The SDK enables Host and Origin validation on loopback.
    allowed_hosts = [
        value.strip()
        for value in os.environ.get("DOCKETWISE_MCP_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    ]
    if not allowed_hosts:
        raise SystemExit(
            "DOCKETWISE_MCP_ALLOWED_HOSTS must be set when DOCKETWISE_MCP_HOST is non-loopback."
        )
    allowed_origins = [
        value.strip()
        for value in os.environ.get("DOCKETWISE_MCP_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def create_serve_app() -> Starlette:
    """Build the stateless HTTP app with environment-only vendor configuration."""
    mcp = create_server()
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=_host(),
        stateless_http=True,
        transport_security=_transport_security(),
    )


async def _serve_streamable_http() -> None:
    import uvicorn

    config = uvicorn.Config(
        create_serve_app(),
        host=_host(),
        port=_port(),
        access_log=False,
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    """Run the selected transport with deferred client configuration."""
    transport = _requested_transport()
    if transport == "stdio":
        create_server().run(transport="stdio")
        return
    if transport == STREAMABLE_HTTP_TRANSPORT:
        asyncio.run(_serve_streamable_http())
        return
    raise SystemExit(
        "Unsupported DOCKETWISE_MCP_TRANSPORT "
        f"{transport!r}; expected 'stdio' or '{STREAMABLE_HTTP_TRANSPORT}'."
    )


if __name__ == "__main__":
    main()
