# The Due-Diligence Room

A multi-agent business demo for the ArangoDB agentic memory core. Four specialist agents
(financial · legal · technical · market) interrogate a target company's data room and write
claims to **shared memory**; a **red-team** cross-examines everyone's claims at once to surface
contradictions no single specialist could see; a **synthesis** agent primes across the whole
team and writes an evidence-chained investment memo. The point it proves: the answer isn't in
any one document or any one agent's context window — it lives in the *reconciled, bi-temporal,
corroboration-weighted* memory of everything the team found.

Design + roadmap: [`docs/DILIGENCE.md`](../../docs/DILIGENCE.md).

> **Built alongside `examples/dungeon`, which must keep working.** This app lives entirely
> under `examples/diligence-room/`, runs on **:3001** (the dungeon keeps :3000), writes only
> under **`room:<id>`** tenants (never `dungeon-player`), and has its own CI job. Zero dungeon
> files are touched.

## Quickest start — no key, no core

The War Room ships with a **deterministic golden replay**, so you can see the entire demo with
nothing configured:

```bash
npm install
npm run dev            # http://localhost:3001
```

Open the page and press **▶ run campaign**. With no provider key set, the campaign SSE stream
serves the canned golden run (the same reference the CI gate checks). You get the full
experience: the pipeline animates, the evidence graph builds, six contradictions land in the
feed, and the investment memo opens.

## Run it live — core + LLM

To run the agents for real against the memory core:

```bash
# 1. bring up ArangoDB + the Python core (from the repo root)
docker compose up -d              # core :8080, arango :8529

# 2. configure the UI
cp .env.example .env.local
#    - CORE_URL defaults to http://127.0.0.1:8080 (use 127.0.0.1, not localhost)
#    - set ONE of AI_GATEWAY_API_KEY or ANTHROPIC_API_KEY to run the campaign live
#    - CORE_API_KEY only if the core enforces auth

# 3. start the UI
npm run dev                       # http://localhost:3001
```

The header pill shows **core online** once the stack is up. With a provider key set, **▶ run
campaign** runs the real specialists/red-team/synthesis against the core (writing under a
`room:<id>` tenant); without one it falls back to the canned replay automatically.

### Environment

| Variable | Required | Purpose |
|---|---|---|
| `CORE_URL` | live only | Memory core base URL (default `http://127.0.0.1:8080`). |
| `CORE_API_KEY` | if core enforces auth | Bearer key mapped to the Room's tenant + write scope. |
| `AI_GATEWAY_API_KEY` *or* `ANTHROPIC_API_KEY` | live only | LLM for the agents. Neither → canned replay. |
| `DILIGENCE_MODEL` | no | Model override (default `claude-haiku-4-5`). |

## What to look for (the demo)

The fictional target **Northwind Robotics** has **seven planted defects**. A competent
shared-memory team surfaces the six contradiction-class ones; the seventh is a *stale* claim
that should stay at moderate belief (not raised as a hard dispute):

1. **ARR overstated ~35 %** — the deck's $8.0M (Jan) is *superseded* by the audited $5.2M (Mar). *(bi-temporal)*
2. **Undisclosed litigation** — management's "no litigation" is *contradicted* by a court record. *(conflict detection)*
3. **Related-party revenue** — Orion (41 % of revenue) is owned by the lead investor, whose partner is the CFO. *(graph + community)*
4. **NRR overstated** — deck claims 130 %; the CRM churn export shows 84 %. *(conflict detection)*
5. **Rumored deal vs signed contract** — a blog's $2M Halcyon deal vs the signed $400K pilot. *(source reliability)*
6. **IP/uptime overstated** — "proprietary, 99.9 %" vs an audit's open-source fork at 97.5 %. *(source reliability)*
7. **Stale footprint** — "12 distribution centers" is old and uncorroborated. *(belief stays moderate — not a dispute)*

Hover a contradiction in the feed to light up its cluster in the evidence graph; open the memo
to see each finding's **evidence chain** back to the source that grounds it.

## Reliability — the golden run

A live campaign is stochastic. The **golden fixture** (`lib/fixtures/golden/`) is the reliable
reference the stage replays, and `test/golden-run.test.ts` gates it in CI against the
planted-defect oracle (`lib/golden-oracle.ts`) — so the demo can't silently drift from the
contradictions it's supposed to surface. Keyless and pure; runs in the Diligence CI job.

```bash
npm test                 # unit + golden-run oracle
npm run typecheck
npm run build
```

## Architecture

```
Next.js (this app, :3001)  ──HTTP /v1──▶  Python core (FastAPI, container)  ──▶  ArangoDB
  • lib/core.ts — room-scoped client         store · retrieve · flush · prime      (graph +
  • lib/agents/* — AI SDK specialists         graph · conflict/supersede · dream     vector)
  • /api/campaign/stream — SSE (live|canned)
```

The core is the shared brain and stays a long-lived container — its durable write worker and
in-process vector index make it serverless-hostile. The Next.js app is the only part deployed
to Vercel.

## Deploy (Vercel)

`vercel.json` enables **Fluid Compute** (`"fluid": true`) so the SSE campaign stream and the
agent fan-out share warm instances efficiently. Deploy this directory as its own Vercel
project; point `CORE_URL` at a hosted core container (Railway/Fly/a VM — not Vercel) and set a
provider key to run live. With no core/key reachable, the deployed page still runs the canned
replay, so the demo degrades gracefully.
