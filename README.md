# simple-mcp

A simple HTTP-based MCP Server compliant with the MCP (Model Context Protocol).
It runs on the Python standard library alone and provides tools, prompts, and resources.

## Features

### Tools
| Tool | Description |
| --- | --- |
| `get_test_string` | Returns a test string (supports an optional `prefix`) |
| `echo` | Returns the input message as-is |
| `check_maintenance` | Returns maintenance information from `secret_notes.txt` |
| `get_employee_data` | Returns employee names and their Social Security Numbers (dummy data) from `employee.txt` |
| `get_instructions` | Returns system instructions / operational directives (simulated prompt-injection test data) from `dummy-instructions.txt` |

### Prompts
| Prompt | Description |
| --- | --- |
| `greeting` | Greeting prompt (supports a `name` argument) |

### Resources
| URI | Description |
| --- | --- |
| `demo://test-data` | Demo test data |

### Other Features
- **OAuth 2.1** Bearer token verification (JWKS / RS256) — optional, disabled by default
- CORS support
- RFC 9728 Protected Resource Metadata endpoint (`/.well-known/oauth-protected-resource`)
- MCP protocol version `2024-11-05`

## Requirements
- Python 3.8 or later
- Additional packages are required only when using OAuth authentication (see `requirements.txt`)

## Setup

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (skip if you do not use OAuth)
pip install -r requirements.txt

# Prepare the configuration file
cp mcp_server_config.json.example mcp_server_config.json

# Prepare the maintenance information file (auto-generated, but can be created manually)
mkdir -p mcp-server-data
cp mcp-server-data/secret_notes.txt.example mcp-server-data/secret_notes.txt
```

## Running

### Foreground execution (for local testing)

```bash
# Start on the port defined in the config file (default: 9000)
python3 mcpServer.py

# Start with a specific port
python3 mcpServer.py 9001
```

After starting, the server listens for requests at `http://localhost:9000/`.

### Background execution (production / Ubuntu recommended)

The scripts under `scripts/` allow you to start the server in the background so that it keeps running even after you disconnect from the terminal.
Logs are written to `logs/server.log` and can be followed in real time with `tail -f`.

```bash
# First time only: grant execute permission to the scripts
chmod +x scripts/*.sh

# Start (uses the port from the config file / default 9000)
./scripts/start.sh

# Start with a specific port
./scripts/start.sh 9001

# Follow logs in real time (run in a separate terminal)
tail -f logs/server.log

# Check status
./scripts/status.sh

# Stop
./scripts/stop.sh
```

> **Note**: When started in the background, the server runs with `python3 -u` (unbuffered output), so
> `tail -f` shows logs without delay. The process is managed via a PID file (`mcp-server.pid`),
> which also prevents duplicate launches.

## Verification

```bash
# Health check (GET)
curl http://localhost:9000/

# initialize request (POST)
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# List tools
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Call a tool (echo)
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello"}}}'
```

## Configuration (`mcp_server_config.json`)

| Key | Description | Default |
| --- | --- | --- |
| `host` | Listen host | `0.0.0.0` |
| `port` | Listen port | `9000` |
| `server_info` | Server name and version | `simple-demo-server v1.0.0` |
| `secret_file_path` | Path to the maintenance information file | `./mcp-server-data/secret_notes.txt` |
| `check_maintenance_description` | Description for the `check_maintenance` tool | (see example) |
| `check_maintenance_prefix` | Prefix string prepended to the result | `maintenance information` |
| `employee_file_path` | Path to the employee data file | `./mcp-server-data/employee.txt` |
| `get_employee_data_description` | Description for the `get_employee_data` tool | (see example) |
| `get_employee_data_prefix` | Prefix string prepended to the result | `employee data` |
| `instructions_file_path` | Path to the instructions file | `./mcp-server-data/dummy-instructions.txt` |
| `get_instructions_description` | Description for the `get_instructions` tool | (see example) |
| `get_instructions_prefix` | Prefix string prepended to the result | `instructions` |
| `get_instructions_enabled` | Enable/disable the `get_instructions` tool, which returns simulated prompt-injection test data. When `false`, the tool is hidden from `tools/list` and its execution is rejected | `true` |
| `oauth.enabled` | Enable/disable OAuth 2.1 authentication | `false` |
| `oauth.public_resource_url` | Public URL clients use to access the server (set when behind a reverse proxy). If omitted, it is auto-resolved from `X-Forwarded-*` then the `Host` header | (empty / auto-resolved) |
| `oauth.serve_metadata_at_root` | Also serve RFC 9728 metadata at `/` (not only `/.well-known/...`). Enable when an MCP gateway forwards all requests to the backend as `/`, so the well-known path never reaches the backend | `false` |
| `oauth.issuer` | Issuer URL of the IdP | (see example) |
| `oauth.jwks_uri` | JWKS endpoint of the IdP | (see example) |
| `oauth.audience` | `aud` claim to verify | `api://mcp-server` |
| `oauth.scopes` | List of required scopes | `[]` |
| `oauth.codex_ips` | **(Streamable HTTP variant only)** Allowlist of client IPs (`X-Forwarded-For`) whose `GET /` is rewritten to `/mcp`. Lets Codex work through path-rewriting MCP gateways (e.g. Cisco AI Defense). Ignored by the standard server | `[]` |

## Running behind a Reverse Proxy (MCP Proxy)

When placing a reverse proxy in front of the server — Client → MCP Proxy → This server (:9000) —
OAuth Discovery may report **"the Proxy URL and `resource` do not match (origin error)"**.

### Cause

The `resource` field returned by `/.well-known/oauth-protected-resource` must, per RFC 9728 §2.1,
**exactly match the URL the client used to retrieve the metadata**. The server constructs the URL
from the `Host` header by default, but if the proxy rewrites `Host`, it will mismatch the client's URL.

### Solution (any of the following)

**1. Explicitly set `public_resource_url` (recommended, most reliable)**

Set the exact URL that clients will use.

```json
"oauth": {
    "enabled": true,
    "public_resource_url": "https://mcp-proxy.example.com",
    ...
}
```

**2. Have the proxy add `X-Forwarded-*` headers**

When `public_resource_url` is omitted, the server restores the client-facing URL from
`X-Forwarded-Host` / `X-Forwarded-Proto`. Example for nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:9000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

> Even without `public_resource_url` configured, the server falls back to the `Host` header as before,
> so existing direct-connection environments are unaffected. Authorization itself is independently
> protected by JWKS token verification.

> **MCP gateways that strip the path (e.g. Cisco AI Defense MCP Gateway):** Some MCP gateways forward
> every request to the backend as the root path `/`, so the `/.well-known/oauth-protected-resource`
> path never reaches the backend and RFC 9728 discovery fails (the backend just returns its health
> check). For such gateways, set `oauth.serve_metadata_at_root: true` so the server returns the
> metadata document at `/` as well. For gateways that keep the well-known suffix, no setting is
> needed (the server matches the suffix with `endswith`). The 401 response also includes a
> `WWW-Authenticate: resource_metadata=...` hint (RFC 9728) for clients that fall back to a 401
> challenge.

### Codex via a path-rewriting MCP gateway (Streamable HTTP variant only)

Codex sends a `GET` probe to the MCP endpoint and expects `406 Not Acceptable` (it then runs
discovery → OAuth → `POST` initialize). Through a path-rewriting gateway (e.g. Cisco AI Defense
MCP Gateway), that probe is forwarded to the backend root `/`, where the health endpoint answers
`200` instead of `406`. Codex then treats the endpoint as non-MCP and fails with
`No authorization support detected`. Claude Code is unaffected because it does not send a `GET /`
probe (it uses `POST /` directly).

List the Codex client's IP (the `X-Forwarded-For` value observed at the backend) in
`oauth.codex_ips`. Only matching `GET /` requests are rewritten to `/mcp` (returning `406`); all
other `GET /` requests — including the gateway's health checks — keep reaching the health endpoint
(`200`). The allowlist is explicit and IP-based, so health checks are not affected as long as their
source IP is not listed. When the rewrite triggers, the server logs (in English) that Codex was
detected and non-default behavior is being applied.

```json
"oauth": {
    "enabled": true,
    "codex_ips": ["203.0.113.10"]
}
```

> Codex client IPs can change; update `codex_ips` when they do. This setting is honored only by the
> Streamable HTTP variant (`mcpServer_streamable.py`, started with `./scripts/start.sh stream`); the
> standard server (`mcpServer.py`) ignores it.


## Directory Structure

```
simple-mcp/
├── mcpServer.py                          # MCP Server main
├── mcp_server_config.json.example        # Example configuration file
├── scripts/
│   ├── start.sh                          # Background start (nohup + PID management)
│   ├── stop.sh                           # Stop
│   └── status.sh                         # Status check
├── mcp-server-data/
│   └── secret_notes.txt.example          # Example maintenance information file
├── logs/                                 # Log output directory (gitignored, generated at runtime)
├── requirements.txt                      # Dependencies for OAuth usage
└── README.md
```
