# Multi-Agent Orchestration

How to use the memory core as a **shared brain** for a pipeline of agents: agent A does
a job and writes what it learned; agent B picks up the next job and starts *warm*, with
A's context — without B re-deriving anything.

This is the pattern the coordination layer (DESIGN.md §14, roadmap MA-1..MA-5) was built
for. Everything here is backed by shipped endpoints and adapters — the
[handoff eval](../core/src/arango_memory/eval/handoff.py) is its executable spec, and
[The Guild](GUILD.md) is the visual demo.

## Mental model (60 seconds)

| Concept | Is | Example |
|---|---|---|
| `tenant_id` | the workspace / job — a hard isolation boundary | `acme-support` |
| `agent_id` | one worker's identity (its private memory) | `researcher-1` |
| shared namespace | a pseudo-agent all workers read/write | `crew::query` |
| insight namespace | distilled strategy, written only by Dream State | `crew::insight` |

Two verbs run **at stage boundaries**; everything else is normal `store` / tool use:

- **`flush`** — block until the previous agent's writes are committed *and* retrievable.
- **`prime`** — assemble the next agent's briefing (history + entities + tool runs).

## Primitives → capability

| You want… | Use | Surface |
|---|---|---|
| B reads A's final writes reliably | `sync` on writes, or `POST /v1/flush` | MA-1 |
| B reads across A + shared tiers in one pass | `read_agent_ids` on retrieve/prime | MA-2 |
| "Brief me for this task" | `POST /v1/prime` | MA-3 |
| A's *conclusions* persist, not just inputs | `captureResponses` (Vercel), or `store` the answer | MA-4 |
| Provenance — who knew this | `agent_id` on every hit | MA-2 |

## Worked pipeline: planner → researcher → writer

Three agents share the tenant `demo` and the `crew::query` tier. Each stage: **flush**
the prior writes, **prime** for its task, do work, **store** results back to the shared
tier. Runnable against a local `docker compose up` core at `http://localhost:8080`.

```bash
CORE=http://localhost:8080
Q='{"tenant_id":"demo","agent_id":"crew::query","access_level":"write"}'

# ── Stage 1: planner writes a plan to the shared tier (sync = readable immediately) ──
curl -s $CORE/v1/store -H 'content-type: application/json' -d '{
  "content": "Plan: research ArangoDB vector limits, then draft a summary.",
  "ctx": '"$Q"', "sync": true }'

# ── Stage 2: researcher primes on the plan, works, writes findings ──
curl -s $CORE/v1/prime -H 'content-type: application/json' -d '{
  "task": "what should I research",
  "ctx": {"tenant_id":"demo","agent_id":"researcher","read_agent_ids":["researcher","crew::query"]}
}'   # → { "context": "## Relevant history\n- Plan: research ArangoDB vector limits…", … }

curl -s $CORE/v1/store -H 'content-type: application/json' -d '{
  "content": "Finding: ArangoGraph cannot enable the vector index; BM25+graph carry retrieval.",
  "ctx": '"$Q"', "sync": true }'

# ── Stage 3: writer primes on plan + findings (both are in crew::query) ──
curl -s $CORE/v1/prime -H 'content-type: application/json' -d '{
  "task": "draft the summary from the research",
  "ctx": {"tenant_id":"demo","agent_id":"writer","read_agent_ids":["writer","crew::query"]}
}'   # → briefing contains BOTH the plan and the finding
```

`flush` is implicit above because every write used `"sync": true`. When writes are async
(the default), call the barrier between stages instead:

```bash
curl -s $CORE/v1/flush -H 'content-type: application/json' \
  -d '{"ctx":{"tenant_id":"demo","agent_id":"writer"},"timeout_ms":5000}'
# → {"status":"flushed"}   (or {"status":"timeout","pending":N})
```

## Per-harness recipes

Just the handoff-specific delta — see each [adapter guide](adapters/README.md) for setup.

**Vercel AI SDK** ([vercel.md](adapters/vercel.md)) — options on the middleware, plus the
standalone helpers between stages:
```ts
import { arangoMemory, prime, flush } from "@arango-memory/vercel";

const model = wrapLanguageModel({ model, middleware: arangoMemory({
  coreUrl, tenantId: "demo", agentId: "researcher",
  readAgentIds: ["researcher", "crew::query"],  // read across own + shared (MA-2)
  syncWrites: true,                              // writes readable at handoff (MA-1)
}) });

await flush({ coreUrl, tenantId: "demo", agentId: "writer" });
const brief = await prime({ coreUrl, task: "draft the summary",
  tenantId: "demo", agentId: "writer", readAgentIds: ["writer", "crew::query"] });
```

**MCP** ([mcp.md](adapters/mcp.md)) — the tool sequence a Claude-Desktop-style agent runs
at a stage boundary: `flush(tenant_id, agent_id)` → `prime(task, tenant_id, agent_id,
read_agent_ids)` → do work → `store(...)`. `search` also takes `read_agent_ids`.

**LangGraph** ([langchain.md](adapters/langchain.md)) — use the in-process `ArangoMemoryNode`
`recall`/`remember` nodes for normal turns; for a stage briefing call the core function
directly (no HTTP hop):
```python
from arango_memory.retrieve.prime import prime
brief = prime(db, task="draft the summary", tenant_id="demo", agent_id="writer",
              read_agent_ids=["writer", "crew::query"])
```

**CrewAI** ([crewai.md](adapters/crewai.md)) — `crew_memory` already reads across
`[own, crew::query, crew::insight]` in one fused pass; each agent writes to its own tier,
reads the crew's:
```python
from arango_memory.crewai import crew_memory
mem = crew_memory(db, tenant_id="demo", crew_id="crew", agent_id="researcher")
mem.query.save("Finding: …")          # → shared tier
hits = mem.interaction.search("what did the crew find")  # spans own + shared + insight
```

## Orchestrator vs. the brain (the seam)

| The **orchestrator** owns (your code / LangGraph / Temporal) | The **brain** provides (this core) |
|---|---|
| Job queue, retries, scheduling | Context assembly (`prime`) |
| Triggering the next agent | Provenance (who wrote each fact) |
| Failure handling, timeouts | History + entities + tool runs |
| Which agent does what, when | Read-your-writes barrier (`flush`) |

**Job/claim semantics are deliberately out of scope** — the core is a memory system, not
a task queue. It hands the next agent everything it needs to *do* the work; deciding
*which* agent runs *when* stays in your orchestrator. (Rationale + the deferred
"atomic entity claim" idea: [ROADMAP.md](ROADMAP.md).)

## Consistency model (the sharp edge)

Writes are **asynchronous by default**: `store` returns `queued` and a memory becomes
retrievable once the worker commits *and* the search view indexes it (≤ ~1s more). Fine
within one agent's turn; at a **handoff**, use a barrier so B can't miss A's final writes:

- `store(..., sync=true)` — commit + make visible before responding.
- `POST /v1/flush` — block until the tenant's queue drains and the view is synced.

Both cover **BM25 + graph**; the **vector arm** updates on its own cadence and isn't
covered — so use `sync`/`flush` at **stage boundaries, not every turn**. Full detail:
DESIGN.md §15.

## See also
- [handoff eval](../core/src/arango_memory/eval/handoff.py) — the executable spec of this
  pattern (writer → reader, scored, CI-gated).
- [GUILD.md](GUILD.md) — the same pattern as a playable game (expendable agents, a live
  briefing screen).
- [api.md](api.md) · [DESIGN.md §14](DESIGN.md) — endpoint reference and design rationale.
