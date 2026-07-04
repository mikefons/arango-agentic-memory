# Documentation

Start here. Pick the doc for what you're doing — they link rather than duplicate.

| You want to… | Read |
|---|---|
| Understand what this is + run it in 5 min | [root README](../README.md) |
| Call the core over HTTP (endpoints, auth, errors, examples) | [api.md](api.md) |
| Run / configure / operate the service (env, jobs, scaling, security) | [ops.md](ops.md) |
| Troubleshoot a problem | [ops.md → Troubleshooting](ops.md#troubleshooting) |
| Wire an adapter (Vercel · LangChain · CrewAI · MCP) | [adapters/](adapters/README.md) |
| Understand the architecture + design decisions | [DESIGN.md](DESIGN.md) |
| See what's planned (multi-agent handoff work packages) | [ROADMAP.md](ROADMAP.md) |
| See what changed | [CHANGELOG.md](../CHANGELOG.md) · build history: [HISTORY.md](HISTORY.md) |
| Contribute (setup, `make ci`, PR flow) | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| Export metrics/traces | [deploy/observability/](../deploy/observability/README.md) |

## How the pieces connect

```
consumer app ──HTTP /v1──▶ Python core (FastAPI) ──AQL──▶ ArangoDB (graph + vector + BM25)
  (Vercel adapter,          ingest · retrieve · lifecycle        durable queue, optional
   MCP, or any client)      · security · telemetry               Redis shared layer
```

- The **core** holds all memory intelligence; the **HTTP `/v1` boundary** is the stable,
  adapter-neutral contract ([api.md](api.md)). In-process Python adapters (LangChain,
  CrewAI) skip the HTTP hop.
- **Authoritative spec:** [DESIGN.md](DESIGN.md). Operational truth: [ops.md](ops.md).
  The running service self-describes at `GET /docs` (OpenAPI).

## New here?

1. Skim the [root README](../README.md) and bring the stack up (`docker compose up`).
2. Try the [api.md](api.md) curl examples against `http://localhost:8080`.
3. For the design rationale, read [DESIGN.md](DESIGN.md) §1–2 (purpose + architecture
   decisions) then the section for the area you're touching.
4. To contribute, follow [CONTRIBUTING.md](../CONTRIBUTING.md).
