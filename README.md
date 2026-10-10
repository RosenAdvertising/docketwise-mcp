# DocketWise MCP

[![CI](https://github.com/RosenAdvertising/docketwise-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/docketwise-mcp/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python MCP server that exposes documented DocketWise API operations to compatible
assistants over stdio. The current implementation registers 14 tools, based on the
`@app.tool` registrations in `docketwise_mcp/server.py`.

## Features

- Read contacts, matters, matter receipts, documents, invoices and invoice charges, matter
  types and statuses, and firm users.
- Create contacts and notes. Both write operations require `confirm=true`.
- Paginate list results across DocketWise's documented 200-record pages, with a maximum of
  1,000 records per call.
- Apply bounded retries for rate limits and transient server errors, including DocketWise's
  `Retry-Header` delay, and limit requests to the documented 120 per minute.
- Use the documented production API endpoint by default, with an optional base URL override.

The implementation follows documented API methods. It does not implement token exchange or
refresh, webhooks, undocumented resources, or inferred error schemas. Contact creation is
marked experimental because the request is documented but its exact response body is not.

## Requirements

- Python 3.12
- An MCP-compatible client
- DocketWise API access and an issued OAuth access token

DocketWise API access is available for Pro and Advanced subscriptions. Contact DocketWise to
request API access. This server uses an already-issued token; it does not obtain or refresh
tokens.

## Install from source

Clone this repository, then run:

```bash
uv sync --locked
```

This installs the project and its locked dependencies in the local virtual environment.

## Configuration

Set these environment variables in the MCP host configuration:

| Variable | Required | Description |
| --- | --- | --- |
| `DOCKETWISE_ACCESS_TOKEN` | Yes | Issued DocketWise OAuth access token. |
| `DOCKETWISE_BASE_URL` | No | API base URL; defaults to `https://app.docketwise.com/api/v1/`. |

The server reads configuration from its process environment. `.env.example` documents the
variable names and contains placeholders only; the server does not load `.env` files.

## MCP client configuration

After cloning and running `uv sync --locked`, use the absolute path to the clone directory in
your MCP client configuration. For example:

```json
{
  "mcpServers": {
    "docketwise": {
      "command": "uv",
      "args": ["--directory", "/path/to/docketwise-mcp", "run", "--locked", "docketwise-mcp"],
      "env": {
        "DOCKETWISE_ACCESS_TOKEN": "your-issued-access-token"
      }
    }
  }
}
```

Replace `/path/to/docketwise-mcp` with the local clone directory and provide the token through
your MCP client's secure environment configuration.

## HTTP mode

Set `DOCKETWISE_MCP_TRANSPORT=streamable-http` to serve stateless Streamable HTTP at
`/mcp`. Stdio remains the default. Both transports use the same vendor configuration
from the process environment; request headers do not supply vendor credentials.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write and delete tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `DOCKETWISE_MCP_ALLOWED_HOSTS` and `DOCKETWISE_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKETWISE_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. |
| `DOCKETWISE_MCP_HOST` | `127.0.0.1` | HTTP bind address. |
| `PORT` | `8080` | HTTP port (integer). |
| `DOCKETWISE_MCP_ALLOWED_HOSTS` | Unset | Comma-separated Host values; required for non-loopback binds. Include ports explicitly or use `host:*`. |
| `DOCKETWISE_MCP_ALLOWED_ORIGINS` | Unset | Comma-separated allowed Origins on non-loopback binds, such as `https://client.example.com`. An absent Origin is accepted; other Origins are refused. The SDK validates loopback Hosts and Origins automatically. |
| `DOCKETWISE_ACCESS_TOKEN` | Unset | Issued DocketWise OAuth access token; required to call vendor tools. |
| `DOCKETWISE_BASE_URL` | `https://app.docketwise.com/api/v1/` | Optional HTTPS vendor API base URL. |

With `DOCKETWISE_ACCESS_TOKEN` exported in the process environment, run:

```bash
DOCKETWISE_MCP_TRANSPORT=streamable-http uv run --locked docketwise-mcp
```

The default endpoint is `http://127.0.0.1:8080/mcp`. Responses keep the SDK's default
SSE support so disconnecting clients cancel pending requests.

## Development

Tests mock all HTTP responses and do not require a live DocketWise token.

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
```

## License

MIT. See [LICENSE](LICENSE).

## Local setup and verification

The server starts and answers MCP initialization and tool listing without credentials.
Tools report a configuration error until the required environment is provided.
Run `docketwise-mcp-setup` for environment-variable instructions, then restart your MCP
launcher with those variables exported. Run `docketwise-mcp-verify` to validate local
configuration (exit 2 if missing or invalid). This command does not contact the
vendor or prove authentication. Neither command saves or prints secret values.

Document download URLs returned by the download-link tool are short-lived secrets
(valid for one hour). Treat them like tokens: use them only in your own client and
keep them out of logs, shared transcripts and public documents.

`DOCKETWISE_BASE_URL` must use HTTPS without userinfo, query or fragment.
