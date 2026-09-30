# MCP Server

`arango_memory.mcp` — a [FastMCP](https://github.com/jlowin/fastmcp) server (**stdio**, or
**Streamable HTTP** as a standalone network service) that exposes the core's `/v1` HTTP API as 11 tools, so MCP clients (Claude
Desktop, Cursor, Windsurf) get agentic memory — including multi-agent handoff — without
writing code.

> **Worked example:** [`examples/mcp-memory/`](../../examples/mcp-memory/) — a runnable demo
> (headless `scenario.py` + Claude Desktop / Cursor configs) showing persistent memory recalled
> across separate sessions.

## Install & run
```bash
pip install "arango-memory[mcp]"
python -m arango_memory.mcp           # stdio; talks to the core over HTTP
```
| Env | Default | Notes |
|---|---|---|
| `ARANGO_MEMORY_CORE_URL` | `http://localhost:8080` | the running core's `/v1` base |
| `ARANGO_MEMORY_API_KEY` | — | bearer credential (static key or JWT) when the core is enforced |

The server is a **client of the core** — start the core first (see [ops.md](../ops.md)).

## Client config (Claude Desktop)
```jsonc
// claude_desktop_config.json
{
  "mcpServers": {
    "arango-memory": {
      "command": "python",
      "args": ["-m", "arango_memory.mcp"],
      "env": { "ARANGO_MEMORY_CORE_URL": "http://localhost:8080" }
    }
  }
}
```

## Streamable HTTP (remote clients)
Run the same server as a standalone HTTP service, for clients that connect by URL (Claude Code,
Cursor, custom agents) or to share one server across machines:
```bash
python -m arango_memory.mcp --transport http            # http://127.0.0.1:8000/mcp
```
| Flag | Env | Default |
|---|---|---|
| `--transport` | `ARANGO_MEMORY_MCP_TRANSPORT` | `stdio` (`http` for this mode) |
| `--host` / `--port` / `--path` | `ARANGO_MEMORY_MCP_HOST` / `_PORT` / `_PATH` | `127.0.0.1` / `8000` / `/mcp` |
| `--allowed-hosts` | `ARANGO_MEMORY_MCP_ALLOWED_HOSTS` | — (comma-separated `Host` values; **required** off localhost) |
| `--allowed-origins` | `ARANGO_MEMORY_MCP_ALLOWED_ORIGINS` | — (comma-separated browser `Origin`s) |
| `--allow-anonymous` | `ARANGO_MEMORY_MCP_ALLOW_ANONYMOUS=1` | off (localhost binds only) |

**Auth is pass-through.** Every request must carry the caller's own `Authorization: Bearer <key or
JWT>` — the same credential the core accepts — or it gets `401`. The server forwards that header to
the core on each tool call, so the core's ABAC applies **per caller**: a tenant-bound key can only
reach its tenant, agent binding and scopes hold (§17, MA-7). The server's own
`ARANGO_MEMORY_API_KEY` is **never** used in HTTP mode, so a network caller can't borrow it. Run the
core in enforced mode (`API_KEYS` / JWT) when the MCP port is reachable by anyone but you.

**Network safety.** It binds `127.0.0.1` by default, with the SDK's DNS-rebinding guard (only local
`Host`/`Origin` values). Binding anything else requires `--allowed-hosts` (the start-up refuses
otherwise), and `--allow-anonymous` is refused off localhost. Put it behind TLS (a reverse proxy — see
**Deploy**) before exposing it. The transport is **stateless** (no session affinity), so it scales
behind a load balancer. `GET /health` answers without auth (liveness only — it doesn't call the core).

**Client config.** Claude Code:
```bash
claude mcp add --transport http arango-memory http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer <your-core-key>"
```
Cursor (`.cursor/mcp.json`):
```jsonc
{ "mcpServers": { "arango-memory": {
    "url": "http://127.0.0.1:8000/mcp",
    "headers": { "Authorization": "Bearer <your-core-key>" } } } }
```
Claude Desktop keeps using stdio (above).

Requires `mcp>=1.14.0` (the `[mcp]` extra pins it); `make mcp-floor` smoke-tests that floor.

### Deploy
The core's container image includes the `mcp` extra, so **one image runs either service**: the
default command is the core; the MCP role is the same image with a different command.

**Docker:**
```bash
docker run -d -p 127.0.0.1:8000:8000 \
  -e ARANGO_MEMORY_CORE_URL=http://<core-host>:8080 \
  -e ARANGO_MEMORY_MCP_HOST=0.0.0.0 \
  -e ARANGO_MEMORY_MCP_ALLOWED_HOSTS='localhost:*,127.0.0.1:*' \
  -e HEALTHCHECK_URL=http://localhost:8000/health \
  ghcr.io/mikefons/arango-agentic-memory/core:<version> \
  python -m arango_memory.mcp --transport http
```
`--host 0.0.0.0` is needed *inside* the container; publish the port on `127.0.0.1` (as above) unless a
proxy fronts it. `HEALTHCHECK_URL` points the image's Docker healthcheck at the MCP port (it defaults
to the core's). The server needs no secrets of its own — callers bring their credentials.

**Compose (local):** `docker compose --profile mcp up` adds an `mcp` service next to the core at
`http://localhost:8000/mcp`.

**TLS via a reverse proxy.** Terminate TLS in front and list the public hostname in
`--allowed-hosts` (the proxy must pass the original `Host` through — Caddy and most proxies do by
default). With [Caddy](https://caddyserver.com/) (automatic certificates):
```
mcp.example.com {
    reverse_proxy mcp:8000
}
```
…and run the MCP container with `ARANGO_MEMORY_MCP_ALLOWED_HOSTS=mcp.example.com`. Clients then use
`https://mcp.example.com/mcp`. Add `ARANGO_MEMORY_MCP_ALLOWED_ORIGINS` only for browser-based clients.

**Railway (or any platform that sets `PORT`).** Add a second service from this repo next to the
core: root directory `core`, the Dockerfile builder, start command
`python -m arango_memory.mcp --transport http`, health-check path `/health`, and variables:
`ARANGO_MEMORY_MCP_HOST=0.0.0.0`, `ARANGO_MEMORY_CORE_URL` (the core's private-network URL),
`ARANGO_MEMORY_MCP_ALLOWED_HOSTS=<the service's public domain>`. The port comes from the platform's
`PORT` (explicit `ARANGO_MEMORY_MCP_PORT` wins). Run the core in enforced mode (`API_KEYS` / JWT),
since the endpoint is public — pass-through auth then confines each caller to their own tenant.

## Tools
Eleven thin wrappers over the endpoints in [api.md](../api.md):

| Tool | Maps to |
|---|---|
| `store` | `POST /v1/store` — persist a memory |
| `search` | `POST /v1/retrieve` — hybrid recall (`mode`; optional `read_agent_ids` for multi-agent, MA-2) |
| `prime` | `POST /v1/prime` — task briefing for a handoff: history + entities + tool runs (MA-3) |
| `flush` | `POST /v1/flush` — barrier: block until queued writes are readable (MA-1) |
| `record_step` | `POST /v1/step` — log a procedural tool step |
| `list_steps` | `GET /v1/steps` — replay steps (optional `tool_name`) |
| `forget` | `POST /v1/forget` — soft-delete (right to be forgotten) |
| `stats` | `GET /v1/stats` — per-tenant collection counts |
| `get_entity` | `GET /v1/entities/{id}` — one entity (with belief/centrality) |
| `list_entities` | `GET /v1/entities` — entities (optional `label`) |
| `seed` | `POST /v1/seed` — bulk-load a profile |

Each takes explicit `tenant_id` / `agent_id` args and respects the core's ABAC.
Embeddings are never returned (§17).
