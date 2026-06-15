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
cp .env.example .env.local         # CORE_URL defaults to http://127.0.0.1:8080
npm install
npm run dev                        # http://localhost:3000
```

The footer shows **core online** once the stack is up.

## Build status (Standard scope, 3.5c-0 → 3.5c-3)

- **3.5c-0 — scaffold** ✅ App shell, locked dark/light theme, typed core client, `/api/health`, docker-compose.
- **3.5c-1 — core loop** ✅ `streamText` + `arangoMemory()` + `useChat`; `look`/`move`/`take` tools; world facts persisted to the core; position/inventory resume across reloads. **Needs `AI_GATEWAY_API_KEY` _or_ `ANTHROPIC_API_KEY`** to play (Gateway preferred; falls back to calling Anthropic directly).
- **3.5c-2 — generative UI** ✅ room scene + inventory cards from tool outputs; the live **knowledge-graph map** (`/api/graph`) — rooms vs lore, edges from `relates_to`, refetched each turn.
- **3.5c-3 — the lie engine** ✅ `talk`/`confront`; NPC testimony + claim entities; exposability (evidence-gated); the **Contradiction Ledger** + trust meters; a caught lie calls **`POST /v1/supersede`** → the false fact vanishes from the live map.
- **Graph Explorer** ✅ a **Play · Graph** tab (`/graph`) — a full interactive visualization of the whole memory graph from ArangoDB (`GET /v1/graph`) via React Flow + elk: themed entity nodes, click-to-inspect, edge-type filter, before/after-supersession toggle, and search.
- **Graph salience** ✅ PageRank centrality (`POST /v1/salience`, in-process — Pregel was removed in ArangoDB 3.12) boosts retrieval and sizes the Graph-Explorer node dots; recomputed on **✦ dream**.
- **Graph communities** ✅ label-propagation clustering (`POST /v1/community`, in-process) hues each Graph-Explorer node dot by its `community`; recomputed **before** each dream so Dream State scopes conflict review to a community.
- **Ontology review** ✅ a **Play · Graph · Ontology** tab (`/ontology`) surfaces the core's relationship-type proposals (`/v1/ontology/*`, §13) for one-click approve/reject + a "✦ scan bonds" trigger. Flag-gated on the core: needs `ONTOLOGY_EVOLUTION=true` (+ a real generator) or the tab shows a disabled note.
- **Dungeon dreams** ✅ a **✦ dream** button runs Dream State consolidation (`POST /v1/dream`) — reviews flagged/well-attested entities, confirms conflicts, distills summaries — shows a report toast and refreshes the graph. A Vercel Cron (`vercel.json`) runs it nightly on deploys. Meaningful conflict-confirm/distillation needs a real background model on the **core**: put `ANTHROPIC_API_KEY=…` and `GENERATION_PROVIDER=anthropic` in `examples/dungeon/.env` (gitignored; read by docker compose — *not* the same file as the UI's `.env.local`), then `docker compose up --build`. Without it the core stays keyless and dreams just review/clear.

- **OG share cards** ✅ a **⧉ share** button opens a generated "Dungeon Run" image (`/api/og`, via `next/og`) — entities/relations counted live from the core, plus items/lies/room from the run.
- **Feature toggles** ✅ all OFF by default; opt in via env or **Vercel Edge Config** (`lib/flags.ts`). Knobs: `DUNGEON_HINT` (DM hint level) and `SCENE_ART`.
- **Scene art** ✅ (gated by `SCENE_ART=1` + `OPENAI_API_KEY` + `BLOB_READ_WRITE_TOKEN`) — `/api/scene` generates a dark-fantasy room image and caches it in Vercel Blob; the room card uses it as a backdrop under the memory glimpse. Off → cards keep the glimpse.

The Showcase polish is complete — all items are config-gated toggles, off by default.

## Deploy (later)

Host the core + ArangoDB on a long-running platform (Fly.io / Railway / Render +
ArangoGraph), set `CORE_URL` + `AI_GATEWAY_API_KEY` in the Vercel project, and
deploy this directory. Concrete config lands when we pick a host.
