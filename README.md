# DocketWise MCP server

[![CI](https://github.com/RosenAdvertising/docketwise-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/docketwise-mcp/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-2026--07--28-7C3AED.svg)](https://modelcontextprotocol.io)

Connect Claude and other MCP clients to DocketWise to work with contacts, matters, documents, invoices and notes.

DocketWise MCP server is a [Model Context Protocol](https://modelcontextprotocol.io) server for DocketWise, immigration case management software. It registers 14 tools that read and write DocketWise records; the two write tools require `confirm=true`. It runs over stdio by default, for desktop clients such as Claude Desktop, and offers an opt-in stateless Streamable HTTP mode that implements MCP specification 2026-07-28. DocketWise credentials stay on the machine that runs the server: they come from environment variables, never from the client. The server follows DocketWise's documented API methods and does not implement token exchange or refresh, webhooks, undocumented resources or inferred error schemas.

## Features

- Read contacts, matters, matter receipts, documents, invoices and invoice charges, matter
  types and statuses, and firm users.
- Create contacts and notes. Both write operations require `confirm=true`.
- Paginate list results across DocketWise's documented 200-record pages, with a maximum of
  1,000 records per call. Matter receipts are read in one request and trimmed to the limit.
- Apply bounded retries for rate limits and transient server errors, including DocketWise's
  `Retry-Header` delay, and limit requests to the documented 120 per minute.
- Use the documented production API endpoint by default, with an optional base URL override.

## Tools

The server registers 14 tools.

| Tool | What it does |
| --- | --- |
| `list_contacts` | List the firm's contacts, optionally only people or only institutions. |
| `get_contact` | Get one contact with their address and notes. |
| `experimental_create_contact` | Create a contact. Requires `confirm=true`. |
| `list_matters` | List the firm's immigration matters. |
| `get_matter` | Get one matter with its client, receipts, assigned staff and notes. |
| `list_matter_receipts` | List the USCIS receipt records attached to one matter. |
| `list_matter_types` | List the firm's matter types and their workflow statuses. |
| `list_matter_statuses` | List the workflow statuses the firm uses for matters. |
| `list_documents` | List the firm's documents, optionally searching their titles. |
| `get_document_download_url` | Create a download link for a document. The link lasts one hour; see [Document download links](#document-download-links). |
| `list_invoices` | List the firm's invoices and their payment balances. |
| `list_invoice_charges` | List the service, expense and fee items on one invoice. |
| `list_users` | List firm users so attorneys and assignees can be identified. |
| `create_note` | Add a note to a matter or client. Requires `confirm=true`. |

Tools whose names start with `experimental_` call DocketWise endpoints whose request or response schema is not fully published. For `experimental_create_contact` the request is documented and the response body is not. These tools send only the fields you supply and require `confirm=true` before writing.

## Requirements

- Python 3.10+
- An MCP client such as Claude Desktop
- DocketWise API access and an issued OAuth access token

DocketWise API access is available for Pro and Advanced subscriptions. Contact DocketWise to
request API access. This server uses an already-issued token; it does not obtain or refresh
tokens.

## Installation

Install [uv](https://docs.astral.sh/uv/), then clone the repository and install its locked dependencies:

```bash
git clone https://github.com/RosenAdvertising/docketwise-mcp.git
cd docketwise-mcp
uv sync --locked
```

## Configuration

DocketWise credentials come from environment variables. Set them in the environment that launches the server, such as the `env` object in your MCP client configuration.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DOCKETWISE_ACCESS_TOKEN` | Yes | None | Issued DocketWise OAuth access token. |
| `DOCKETWISE_BASE_URL` | No | `https://app.docketwise.com/api/v1/` | API base URL. Must use HTTPS, without userinfo, query or fragment. |

`.env.example` documents the variable names and contains placeholders only; the server does not load `.env` files.

The server starts and answers MCP initialization and tool listing without credentials. Tools report a configuration error until the required environment is provided. Two commands help with setup:

```bash
uv run docketwise-mcp-setup
uv run docketwise-mcp-verify
```

`docketwise-mcp-setup` prints environment-variable instructions. `docketwise-mcp-verify` validates the local configuration and exits with code 2 if it is missing or invalid; it does not contact DocketWise or prove authentication. Neither command saves or prints secret values.

## Usage with Claude Desktop

Add the server to Claude Desktop's configuration file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "docketwise": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/absolute/path/to/docketwise-mcp", "docketwise-mcp"],
      "env": {
        "DOCKETWISE_ACCESS_TOKEN": "replace-with-your-access-token"
      }
    }
  }
}
```

Replace `/absolute/path/to/docketwise-mcp` with the path of your clone. Keep real credentials in your client's protected environment or secret store. Restart Claude Desktop after saving. Any other stdio MCP client uses the same command and arguments.

## HTTP mode

Stdio is the default. Set `DOCKETWISE_MCP_TRANSPORT=streamable-http` to serve the stateless Streamable HTTP transport from MCP specification 2026-07-28 at `/mcp`. Each request stands alone: no initialization handshake and no `Mcp-Session-Id`. Clients on earlier protocol versions are served on the same endpoint.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `DOCKETWISE_MCP_ALLOWED_HOSTS` and `DOCKETWISE_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKETWISE_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. |
| `DOCKETWISE_MCP_HOST` | `127.0.0.1` | Bind address. `127.0.0.1`, `localhost` and `::1` use the SDK's built-in Host and Origin checks; any other value requires `DOCKETWISE_MCP_ALLOWED_HOSTS`. |
| `PORT` | `8080` | Port; must be an integer. |
| `DOCKETWISE_MCP_ALLOWED_HOSTS` | unset | Comma-separated `Host` header values accepted on a non-loopback bind, such as `mcp.example.com:8080` or `mcp.example.com:*`. |
| `DOCKETWISE_MCP_ALLOWED_ORIGINS` | unset | Comma-separated `Origin` values accepted on a non-loopback bind, such as `https://client.example.com`. Requests without an `Origin` header are accepted; any other `Origin` is refused, including when this is unset. |

DocketWise credentials come from the same configuration as stdio (see [Configuration](#configuration)), never from the request.

With `DOCKETWISE_ACCESS_TOKEN` exported in the process environment, run:

```bash
DOCKETWISE_MCP_TRANSPORT=streamable-http PORT=8080 uv run --locked docketwise-mcp
```

Point the MCP client at `http://127.0.0.1:8080/mcp`. Responses use the SDK's default SSE behavior, so a client that disconnects cancels its pending request.

## Error handling

A failed tool call returns an MCP error result (`isError`) with a fixed message. DocketWise's own response text is never returned to the client and is not written to the log.

| Situation | The message begins |
| --- | --- |
| Missing or invalid configuration | `docketwise is not configured or its configuration is invalid.` It names `DOCKETWISE_ACCESS_TOKEN` and the `docketwise-mcp-setup` command. |
| HTTP 401 | `Authentication failed.` |
| HTTP 403 | `Access denied.` |
| HTTP 404 | `Resource not found.` |
| HTTP 429 | `Rate limit reached.` |
| HTTP 5xx | `Service unavailable.` |
| Any other HTTP error | `Request rejected or response invalid.` |
| Arguments that fail the tool's input schema | `Invalid tool input or response.` |
| A list endpoint that does not return a list | `The operation could not be completed.` (`Invalid tool input or response.` for `list_matter_receipts`) |
| Network failure or timeout | `Connection failed; the operation outcome may be unknown.` |

Input problems have their own messages: a write tool called without `confirm=true` returns `Confirmation required: set confirm=true to ...`, `create_note` without a `matter_id` or `client_id` says one is required, and a record ID that is not a plain identifier is rejected before any request is sent.

Requests answered with HTTP 429 or 5xx are retried up to 3 times. The wait before a retry is the `Retry-Header` value on a 429 when it is a number, and otherwise 0.5, 1 and 2 seconds. Network failures are not retried. Requests time out after 30 seconds, and the server keeps its own traffic within DocketWise's documented limit of 120 requests per minute.

At startup the server exits with a message and status 1 on an unsupported `DOCKETWISE_MCP_TRANSPORT`, a `PORT` that is not an integer, or a non-loopback `DOCKETWISE_MCP_HOST` without `DOCKETWISE_MCP_ALLOWED_HOSTS`. Missing credentials do not stop the server from starting; tool calls report the configuration error instead.

## Document download links

`get_document_download_url` returns a short-lived link that is valid for one hour. Treat it like a token: use it only in your own client and keep it out of logs, shared transcripts and public documents.

## Testing

The test suite runs offline and needs no DocketWise account: every DocketWise API call is intercepted at the HTTP layer with `respx`. It covers each tool's requests and responses, input validation, error handling, credential loading, pagination and retries, the stdio server, and the Streamable HTTP transport including the 2026-07-28 wire format, Host and Origin checks and stateless requests.

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
```

CI runs the suite on every push and pull request to `main`.

## License

MIT. See [LICENSE](LICENSE).
