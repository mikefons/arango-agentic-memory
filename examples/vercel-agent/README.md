# vercel-agent-example

A minimal reference agent that wires the **Vercel AI SDK** to the ArangoDB
agentic-memory core through the `@arango-memory/vercel` middleware. It's the
manual / nightly end-to-end check for Step 3.5 — a real `generateText` loop
flowing through **adapter → core → ArangoDB** (the deterministic CI gate lives
in `core/` as the simulation harness).

What it demonstrates each run:
- **Retrieve + inject** — turn 2 answers using a fact stated only in turn 1.
- **Durable store** — every turn is persisted to the core (async write path).
- **Procedural memory** — the `weather` tool call is captured as a `step`.

## Prerequisites
- The core + ArangoDB running (from the repo root): `docker compose up -d`
  (core on `http://localhost:8080`).
- An Anthropic API key.

## Run
```bash
# 1) Build the adapter it depends on (dist is gitignored):
cd ../../packages/vercel && npm install && npm run build && cd -

# 2) Install + run the example:
npm install
ANTHROPIC_API_KEY=sk-ant-... npm start
```

Optional env:
- `MODEL` — Anthropic model id (default `claude-sonnet-4-6`).
- `ARANGO_MEMORY_CORE_URL` — core URL (default `http://localhost:8080`).

## Verify memory landed
```bash
curl 'http://localhost:8080/v1/steps?tenant_id=demo-user&agent_id=assistant'
```
You should see the `weather` tool recorded as procedural memory.

> Roadmap: a full Next.js chat UI on top of this loop is a later step (3.5c).
