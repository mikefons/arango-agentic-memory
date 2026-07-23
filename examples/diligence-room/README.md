# The Due-Diligence Room

A multi-agent business demo for the ArangoDB agentic memory core: specialist agents
(financial · legal · technical · market) interrogate a target company's data room, write
claims to **shared memory**, disagree and correct each other **over time**, then hand off to
a **red-team** (finds contradictions across everyone) and a **synthesizer** (an
evidence-chained investment memo). The point it proves: the answer isn't in any one document
or any one agent's context — it lives in the *reconciled, bi-temporal, corroboration-weighted*
memory of everything the team found.

Design + roadmap: [`docs/DILIGENCE.md`](../../docs/DILIGENCE.md).

> **Built alongside `examples/dungeon`, which must keep working.** This app lives entirely
> under `examples/diligence-room/`, runs on **:3001** (the dungeon keeps :3000), and writes
> only under **`room:<id>`** tenants — never `dungeon-player`. See the isolation rules in
> DILIGENCE.md.

## Status

- **DR-0a — scaffold** ✅ Next.js app, typed core client (`lib/core.ts`, room-scoped),
  `/api/health`, a landing page that shows **core online/offline**. Its own CI job.

Next: DR-0b (data-room fixtures) → DR-0c (claim model) → DR-1 (the specialist + red-team agents).

## Run it locally

```bash
# 1. bring up ArangoDB + the Python core (from the repo root)
docker compose up -d              # core :8080, arango :8529

# 2. configure + start the UI
cp .env.example .env.local        # CORE_URL defaults to http://127.0.0.1:8080
npm install
npm run dev                       # http://localhost:3001
```

The landing page shows **core online** once the stack is up.

## Architecture

```
Next.js (this app, :3001)  ──HTTP /v1──▶  Python core (FastAPI, container)  ──▶  ArangoDB
  • lib/core.ts — room-scoped client          store · retrieve · flush · graph      (graph +
  • (DR-1) specialist agents on AI SDK         conflict/supersede · dream · …         vector)
```

The core is the shared brain and stays a long-lived container (its durable write worker +
in-process vector index make it serverless-hostile). Vercel Workflows / eve (later) only
schedule the agents and call the same `/v1` verbs.
