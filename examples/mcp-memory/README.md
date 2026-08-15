# MCP memory — a shared brain across sessions

The `arango-memory` core ships a standard **MCP server** (`python -m arango_memory.mcp`, 11
tools over stdio). This example demonstrates the one thing MCP uniquely shows: an assistant's
memory is a **service**, not something baked into one app or one context window. Drop the server
into Claude Desktop / Cursor / Windsurf and your assistant remembers you **across sessions** — and
across clients.

It proves the three capabilities the [LongMemEval benchmark](../../docs/DESIGN.md) measures:

| you say (session 1) | you ask later (session 2, empty context) | capability |
|---|---|---|
| "I'm allergic to shellfish." | "Is there anything I can't eat?" → *shellfish* | **persistence** |
| "My sister Mira is visiting." | "Who is Mira?" → *your sister* | **entity graph** |
| "I moved from Munich to Berlin in March." | "Where do I live now?" → **Berlin** | **supersession** |

The money moment is the last one: both "Munich" and "Berlin" are stored, so a naive vector store
can surface the stale answer. This one says **Berlin** — the newer memory, ranked over the old one
by the graph (exactly the `knowledge-update` gain the benchmark isolates).

## Run the headless demo (90 seconds)

**1. Start a core** (ArangoDB + the API) from the repo root:

```bash
docker compose up
```

For a real semantic demo, give the core real embeddings (`EMBEDDING_PROVIDER=openai` +
`OPENAI_API_KEY` in `core/.env`). The recall queries share no keywords with the facts, so
retrieval has to match by *meaning* — that needs real embeddings.

**2. Run the scenario** (from this directory):

```bash
python scenario.py
```

It drives the **real MCP server over stdio** — the same way Claude Desktop does — as two separate
sessions, each spawning its own server process:

```
── Session 1 · a fresh MCP server process — STORE ──
  → store  "I'm allergic to shellfish."
  → store  "My sister Mira is visiting next week."
  → store  "I moved from Munich to Berlin in March."
  ✓ committed + flushed

── Session 2 · a DIFFERENT server process, empty context — RECALL ──
  » "Is there anything I can't eat?"
     ✓ recalled: I'm allergic to shellfish.
       └ persistence — it never saw session 1's context
  » "Remind me who Mira is?"
     ✓ recalled: My sister Mira is visiting next week.
       └ entity graph — resolves the person across mentions
  » "Where do I live now?"
     ✓ recalled: I moved from Munich to Berlin in March.
       └ supersession — Berlin is newer than the stored Munich

✓ session 2 recalled 3/3 with zero shared context
```

Two different server processes shared nothing but the backend — so the memory clearly lived
there the whole time, not in any window.

## Run it live in Claude Desktop

1. Copy the block from [`claude_desktop_config.json`](./claude_desktop_config.json) into your
   Claude Desktop config, replacing `ABS/PATH/TO` with this repo's absolute path. (Cursor /
   Windsurf: see [`cursor_config.md`](./cursor_config.md).)
2. **Fully quit and reopen** Claude Desktop. The `arango-memory` tools appear under 🔌.
3. In a chat: *"Quick notes about me: I'm allergic to shellfish, my sister Mira visits next week,
   and I moved from Munich to Berlin in March."* → Claude calls `store`.
4. **Quit Claude Desktop, reopen it, start a new chat** — a cold context. Ask: *"Where should we
   get dinner, and where do I live these days?"* → Claude calls `search` and answers with the
   shellfish avoidance and **Berlin**.
5. Show the receipts: ask *"list the entities you know about me"* (the `list_entities` tool) to
   reveal the `Mira` person-node and the graph it built — proof it's real state, not a prompt trick.

## How it works

```
scenario.py ──stdio (MCP)──▶ python -m arango_memory.mcp ──HTTP──▶ core API ──▶ ArangoDB
 (MCP client)                (the MCP server, 11 tools)         (/v1/store,/retrieve,…)
```

The server is a thin stdio→HTTP wrapper ([`core/src/arango_memory/mcp/`](../../core/src/arango_memory/mcp/)),
so the memory is genuinely shared: any client, any host, pointed at the same core with the same
`tenant_id`, reads and writes one brain.

The store→recall sequence lives in `scenario.py` as transport-agnostic `run_store` / `run_recall`
functions, and is smoke-tested keyless in
[`core/tests/test_mcp.py`](../../core/tests/test_mcp.py) (`test_personal_assistant_recall_across_sessions`)
so the example can't rot.

## Honest notes

- **Real embeddings for the live demo.** Under the keyless `FakeEmbedder`, retrieval is lexical,
  so the natural-language queries won't connect. The CI smoke test uses keyword-overlapping
  queries for determinism; the demo uses natural language + `openai` embeddings.
- **Supersession is ranking, not a hard edge here.** "Berlin" wins because it's the newer memory
  and the graph ranks it above the stale "Munich" mention; the older fact is retained, not
  deleted. (Explicit bi-temporal `Supersedes` edges + Dream State consolidation are a core
  feature — see DESIGN §13 — but aren't exposed as an MCP tool.)
- **Auth.** The core runs open by default; set `ARANGO_MEMORY_API_KEY` only if you've enabled it.
