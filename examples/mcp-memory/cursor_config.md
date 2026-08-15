# Cursor / Windsurf / any MCP client

The `arango-memory` server speaks standard MCP over stdio, so any MCP-capable host runs it —
the only thing that changes between hosts is *where* the server block lives.

## Cursor

`Settings → MCP → Add new global MCP server` (or edit `~/.cursor/mcp.json`) and add:

```json
{
  "mcpServers": {
    "arango-memory": {
      "command": "uv",
      "args": ["run", "--project", "ABS/PATH/TO/arango-agentic-memory/core",
               "python", "-m", "arango_memory.mcp"],
      "env": { "ARANGO_MEMORY_CORE_URL": "http://localhost:8080" }
    }
  }
}
```

Replace `ABS/PATH/TO` with the absolute path to this repo. Reload the MCP server; the tools
appear in Cursor's tool list.

## Windsurf

Same block, in `~/.codeium/windsurf/mcp_config.json`.

## Notes

- **A core must be running** — the server is a thin stdio→HTTP wrapper. `docker compose up`
  from the repo root brings up ArangoDB + the core API on `:8080`.
- **Auth** — only set `ARANGO_MEMORY_API_KEY` if the core enforces auth (it runs open by
  default). See `docs/auth.md`.
- **pip install instead of a checkout?** Swap the command for
  `"command": "python", "args": ["-m", "arango_memory.mcp"]`.
- **The shared-brain trick** — point two different hosts (Claude Desktop *and* Cursor) at the
  same core with the same `tenant_id`, and they read/write one brain. What you tell one, the
  other knows.
