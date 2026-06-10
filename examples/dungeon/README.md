# 🏰 Memory Dungeon

A text-adventure where the world **persists across sessions** and the **NPCs lie** —
the reference agent + Next.js UI for the ArangoDB agentic memory core (DESIGN.md §3.5c).

The dungeon's state isn't in React or a SQL row: it lives in the agentic-memory
graph. Rooms, items, and NPCs are **entities**; the map is the **knowledge graph**;
tool calls become **procedural memory**; and catching a lying NPC is the backend's
**bi-temporal supersession + conflict detection** made playable.

> Design of record: [`docs/mockups/dungeon-ui.html`](../../docs/mockups/dungeon-ui.html) — open it in a browser (has a light/dark toggle).

## Architecture

```
Next.js (this app, on Vercel)  ──HTTP──▶  Python core (FastAPI)  ──▶  ArangoDB
  • AI SDK: streamText + tools                /v1/store /retrieve         (graph + vector)
  • arangoMemory() middleware                 /step /entity ...
  • generative-UI cards, map, dossier
```

The core is long-lived (durable write worker), so it runs as a container, not on
serverless. The Next.js ↔ core boundary is the existing `/v1` HTTP contract.

## Run it locally

```bash
# 1. bring up ArangoDB + the Python core
docker compose up --build          # arango :8529, core :8080

# 2. configure + start the UI
cp .env.example .env.local         # CORE_URL defaults to http://localhost:8080
npm install
npm run dev                        # http://localhost:3000
```

The footer shows **core online** once the stack is up.

## Build status (Standard scope, 3.5c-0 → 3.5c-3)

- **3.5c-0 — scaffold** ✅ App shell, locked dark/light theme, typed core client, `/api/health`, docker-compose.
- **3.5c-1 — core loop** ✅ `streamText` + `arangoMemory()` + `useChat`; `look`/`move`/`take` tools; world facts persisted to the core; position/inventory resume across reloads. **Needs `AI_GATEWAY_API_KEY`** to play.
- **3.5c-2 — generative UI** ✅ room scene + inventory cards from tool outputs; the live **knowledge-graph map** (`/api/graph`) — rooms vs lore, edges from `relates_to`, refetched each turn.
- **3.5c-3 — the lie engine** ⏳ `talk`/`confront`; testimony with bi-temporal `valid_time`; the Contradiction Ledger + `supersede`.

Deferred to a Showcase follow-up: the nightly "dungeon dreams" Cron, generative
scene art (Blob), OG share cards, Edge Config knobs, and a **direct-provider
fallback** (`@ai-sdk/anthropic` via `ANTHROPIC_API_KEY`) so the app can be
play-tested without a Vercel AI Gateway key.

## Deploy (later)

Host the core + ArangoDB on a long-running platform (Fly.io / Railway / Render +
ArangoGraph), set `CORE_URL` + `AI_GATEWAY_API_KEY` in the Vercel project, and
deploy this directory. Concrete config lands when we pick a host.
