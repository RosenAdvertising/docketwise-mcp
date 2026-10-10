"""Validate resource identifiers before composing URL paths."""

import math
import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

from mcp.server.mcpserver.exceptions import ToolError

_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]+")


def path_id(value: str | int | float | UUID, parameter: str) -> str:
    """Validate the identifier before encoding it as one URL segment."""
    # JSON represents whole-number IDs as either integers or floats. Preserve
    # integer precision and let the SDK enforce each tool's numeric constraints.
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if (
        isinstance(value, bool)
        or not isinstance(value, str | int | UUID)
        or not _PLAIN_ID.fullmatch(str(value))
    ):
        raise ToolError(
            f"{parameter} must be a non-empty plain identifier using only ASCII letters, "
            "digits, underscores, or hyphens. Supply the record ID, not a URL or encoded path."
        )
    return quote(str(value), safe="")


_TOOL_PATH_IDS: dict[str, tuple[str, ...]] = {
    "get_contact": ("contact_id",),
    "get_matter": ("matter_id",),
    "list_matter_receipts": ("matter_id",),
    "get_document_download_url": ("document_id",),
    "list_invoice_charges": ("invoice_id",),
}


def validate_path_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Keep actionable ID errors ahead of SDK coercion, without changing schemas."""
    for parameter in _TOOL_PATH_IDS.get(name, ()):
        if parameter not in arguments:
            continue  # The SDK reports missing required arguments.
        path_id(arguments[parameter], parameter)
