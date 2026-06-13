# Adapter Guides

The memory core is consumed through thin adapters — all memory intelligence lives
in the core (DESIGN.md §19/§21); adapters are transport/glue. Pick the one for your
stack:

| Adapter | Surface | Transport | Guide |
|---|---|---|---|
| **Vercel AI SDK** | `LanguageModelV2Middleware` (retrieve→inject, store, capture tools) | HTTP `/v1` | [vercel.md](vercel.md) |
| **LangChain / LangGraph** | `Retriever` · `ChatMessageHistory` · graph nodes | In-process Python | [langchain.md](langchain.md) |
| **CrewAI** | crew memory `Storage` (G-Memory tiers) | In-process Python | [crewai.md](crewai.md) |
| **MCP server** | 9 MCP tools for Claude Desktop / Cursor / Windsurf | stdio → HTTP `/v1` | [mcp.md](mcp.md) |

- **TS / Vercel** talks to the core over the HTTP boundary ([`api.md`](../api.md)).
- **Python** adapters (LangChain, CrewAI) run **in-process** with the core — no HTTP
  hop — and are gated behind optional extras.
- The **MCP server** wraps the HTTP API as tools.

All adapters are tenant/agent-scoped and respect the core's ABAC (`access_level`).
