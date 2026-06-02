# ArangoDB Agentic Memory

Persistent, relational memory for AI agents — built on ArangoDB (graph + vector + full-text in one engine), with a Python core and a thin Vercel AI SDK adapter.

> **Status:** Pre-implementation scaffold (Step 0 — walking skeleton). See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design specification.

## Architecture (v1)

```
┌─────────────────────┐     HTTP      ┌──────────────────────────┐     AQL     ┌───────────┐
│  Vercel AI SDK app  │ ────────────▶ │   Python core (FastAPI)  │ ──────────▶ │ ArangoDB  │
│  @arango-memory/    │   /v1/store   │   ingest · retrieve ·    │             │ graph +   │
│  vercel middleware  │   /v1/retrieve│   lifecycle · telemetry  │             │ vector +  │
└─────────────────────┘               └──────────────────────────┘             │ BM25      │
                                                                                └───────────┘
```

- **Python-first core** (`core/`) — all memory intelligence: schema, ingestion, retrieval, consolidation, decay.
- **Thin TypeScript client** (`packages/vercel/`) — a `LanguageModelV4Middleware` that retrieves-and-injects before a turn and durably stores after. No memory logic.

v1 ships the core + Vercel adapter only. MCP, LangChain/LangGraph, and CrewAI adapters are deferred to v2 (the core API is kept adapter-neutral so they're additive).

## Quick start (local dev)

```bash
cp .env.example .env          # OPENAI_API_KEY, ANTHROPIC_API_KEY, ARANGO_LICENSE_KEY
docker compose up -d          # ArangoDB (Enterprise) + Python core sidecar
# core API: http://localhost:8080  ·  ArangoDB UI: http://localhost:8529
```

> Uses the ArangoDB **Enterprise** image (`arangodb/enterprise:3.12.9.1`) for
> vector-index auto-training. Set `ARANGO_LICENSE_KEY` in `.env`; it runs in
> evaluation mode without one.

### Secret-scanning hook (one-time per clone)

A gitleaks pre-commit hook blocks commits containing hardcoded secrets.

```bash
brew install gitleaks pre-commit   # or: pip install pre-commit
pre-commit install                 # activates the hook for this clone
```

Manual scan: `gitleaks dir . --redact`

### Core (Python, uv)

```bash
cd core
uv sync
uv run uvicorn arango_memory.api.app:app --reload --port 8080
```

### Vercel adapter (TypeScript, pnpm)

```bash
cd packages/vercel
pnpm install
pnpm build
```

## Repository layout

```
docs/DESIGN.md         Authoritative design spec (rev 2)
core/                  Python core (uv)
packages/vercel/       Thin TS client middleware (pnpm)
docker-compose.yml     ArangoDB + core for local dev
```

## License

TBD
