# 🏰 The Guild

A text-adventure about **multi-agent handoff**: a guild sends **expendable heroes**
one at a time into a keep where the **NPCs lie**, and the only thing that survives a
hero's death is what they wrote to the guild's **shared memory**. The reference agent +
Next.js UI for the ArangoDB agentic memory core (DESIGN.md §3.5c).

Nothing lives in React or a SQL row: the world lives in the agentic-memory graph.
Rooms, items, and NPCs are **entities**; the map is the **knowledge graph**; each
hero's actions become **procedural memory**; catching a lying NPC is the backend's
**bi-temporal supersession + conflict detection** made playable — and because the guild
remembers across heroes, the traitor can only be caught by **evidence that outlives the
hero who found it**.

## The loop

```
descend (new hero, own agent_id, fresh torch)
  → explore & interrogate — findings written to the shared guild ledger (sync, MA-1)
  → torch (context window) burns down
  → the Chronicler flushes + writes a summary (MA-1 barrier)
  → Handoff Briefing: the next hero is primed from memory (MA-3), you shape it
  → next hero inherits the ledger (read_agent_ids, MA-2) — but not the transcript
  → … until the guild has caught enough of the traitor's lies to accuse them
```

![The Guild loop](../../docs/guild-loop.gif)
<!-- TODO(E-5): record docs/guild-loop.gif — one full expedition → chronicle → briefing → next hero. -->

> Design of record: [`docs/mockups/dungeon-ui.html`](../../docs/mockups/dungeon-ui.html) — open it in a browser (has a light/dark toggle).

> **The Guild** redesign — expendable heroes, a torch-as-context-window, a live Handoff
> Briefing screen, and a traitor you can only catch across expeditions.
> Design + work packages: [`docs/GUILD.md`](../../docs/GUILD.md).

## Architecture

```
Next.js (this app, on Vercel)  ──HTTP──▶  Python core (FastAPI)  ──▶  ArangoDB
  • AI SDK: streamText + tools                /v1/store /retrieve         (graph + vector)
  • arangoMemory() middleware                 /step /entity ...
  • generative-UI cards, map, dossier
```

The core is long-lived (durable write worker), so it runs as a container, not on
serverless. The Next.js ↔ core boundary is the existing `/v1` HTTP contract.

**Fluid Compute** is enabled (`"fluid": true` in `vercel.json`) — the `/api/chat` route
streams the turn, then commits memory to the core *after* the response is sent, which is
exactly Fluid's post-response + warm-instance model. It's a hosting optimization; the game
plays identically with it off.

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
- **Expeditions (The Guild, E-1)** ✅ heroes are **expendable agents**: each expedition is a fresh hero (its own `agent_id`) with a **torch** (turn budget = context window). When the torch dies, the **Chronicler** writes a summary to the shared `guild::query` ledger (a `sync` write, MA-1) and the next hero descends with an empty transcript — but reads the guild's ledger via `readAgentIds: [heroId, guild::query]` (MA-2). "Flee" retires a hero without chronicling (its findings are lost). This is the multi-agent handoff, playable.
- **Handoff Briefing (The Guild, E-2)** ✅ between expeditions, a **briefing screen** renders `POST /v1/prime` (MA-3) live — the incoming hero's inherited **history** (with `you`/`guild` provenance badges), **key entities**, and **prior tool runs**, filling a **token-budget bar** you can **pin/drop** items against. The chronicle first calls `POST /v1/flush` (MA-1 barrier) so the briefing includes the departing hero's last turns. You watch memory become context.
- **Hero personas (The Guild, E-3)** ✅ each expedition's hero gets a deterministic **persona** — a name, glyph, and voice (cowardly bard, literal golem, arrogant knight, …) that colors the DM's narration — so consecutive heroes read as different characters inheriting one ledger. NPCs react to being re-questioned by "the guild". The memory `agent_id` stays `hero-N`; the persona is display + prompt only.
- **Traitor endgame (The Guild, E-4)** ✅ a **win condition**: the merchant **Saro** is the culprit, his lies scattered so no single torch can catch them all. The `accuse` tool resolves from the **persistent `/v1/graph`** (superseded lie-subjects), so caught lies **accumulate across expeditions** — you win by accusing Saro once the guild has exposed enough (threshold 4). A wrong or unproven accusation is **fatal** (the hero perishes, un-chronicled) and leaves NPCs warier. Winning shows the confession + an **evidence chain** (truth ⇒ lie) built from the graph. Unwinnable in one expedition — enforced by a content test.
- **Meta-progression + onboarding (The Guild, E-5)** ✅ a **Guild Ledger** in the dossier shows what compounds across runs — expeditions sent, heroes lost, map filled, claims heard — and a **case-board meter** toward the accusation threshold that reads the *persistent* graph (evidence from heroes long dead). The **map is two-tone**: rooms the guild has ever mapped (dim memory) vs the rooms *this* hero has stood in (bright context) — memory-vs-context as cartography. A first-load **3-beat intro** teaches the loop, a one-line hint points at the ledger on the first catch, and the share card carries the hero's persona + expedition. Pure logic in `lib/guild.ts` (unit-tested).
- **Feature toggles** ✅ all OFF by default; opt in via env or **Vercel Edge Config** (`lib/flags.ts`). Knobs: `DUNGEON_HINT` (DM hint level) and `SCENE_ART`.
- **Scene art** ✅ (gated by `SCENE_ART=1` + `OPENAI_API_KEY` + `BLOB_READ_WRITE_TOKEN`) — `/api/scene` generates a dark-fantasy room image and caches it in Vercel Blob; the room card uses it as a backdrop under the memory glimpse. Off → cards keep the glimpse.

The Showcase polish is complete — all items are config-gated toggles, off by default.

## Deploy

Host the core + ArangoDB on a long-running platform (Fly.io / Railway / Render +
ArangoGraph), then set these in the Vercel project and deploy this directory:

- `CORE_URL` — the core's public URL (no trailing slash).
- `AI_GATEWAY_API_KEY` — from https://vercel.com/ai-gateway.
- `CORE_API_KEY` — **lock down the public core.** Left keyless, anyone who finds
  `CORE_URL` can read/write your memory. Set the core's `API_KEYS` to map a key to
  this app's tenant + write scope, and put the same key here:
  - on the core: `API_KEYS={"<key>":{"tenant_id":"dungeon-player","scope":"write"}}`
  - in Vercel: `CORE_API_KEY=<key>`

  The tenant **must** be `dungeon-player` (the tenant this app uses); `/health` and
  `/ready` stay open so the status banner works without the key.
