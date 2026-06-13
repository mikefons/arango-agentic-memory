# MCP Server

`arango_memory.mcp` — a [FastMCP](https://github.com/jlowin/fastmcp) **stdio**
server that exposes the core's `/v1` HTTP API as 9 tools, so MCP clients (Claude
Desktop, Cursor, Windsurf) get agentic memory without writing code.

## Install & run
```bash
pip install "arango-memory[mcp]"
python -m arango_memory.mcp           # stdio; talks to the core over HTTP
```
| Env | Default | Notes |
|---|---|---|
| `ARANGO_MEMORY_CORE_URL` | `http://localhost:8080` | the running core's `/v1` base |

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

## Tools
Nine thin wrappers over the endpoints in [api.md](../api.md):

| Tool | Maps to |
|---|---|
| `store` | `POST /v1/store` — persist a memory |
| `search` | `POST /v1/retrieve` — hybrid recall (`mode`: `lite`/`full`) |
| `record_step` | `POST /v1/step` — log a procedural tool step |
| `list_steps` | `GET /v1/steps` — replay steps (optional `tool_name`) |
| `forget` | `POST /v1/forget` — soft-delete (right to be forgotten) |
| `stats` | `GET /v1/stats` — per-tenant collection counts |
| `get_entity` | `GET /v1/entities/{id}` — one entity (with belief/centrality) |
| `list_entities` | `GET /v1/entities` — entities (optional `label`) |
| `seed` | `POST /v1/seed` — bulk-load a profile |

Each takes explicit `tenant_id` / `agent_id` args and respects the core's ABAC.
Embeddings are never returned (§17).
