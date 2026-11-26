# Roadmap — Multi-Agent Handoff ("Agent Brain")

**Goal:** close the gap between today's system (excellent single-agent memory) and the
project's end state — a centralized memory core where **agent A writes, agent B picks up
the next job and retrieves the context it needs**, plugged into harnesses (Vercel AI SDK,
MCP, LangChain, CrewAI) as a robust shared brain for large-scale orchestration.

**Where this comes from:** a full assessment of DESIGN.md + code (2026-07-04) against
that goal. The storage/retrieval/security/durability foundations are done and strong;
what's missing is a thin coordination layer. Three load-bearing findings drive the order:

1. **No read-your-writes** — `store` returns `"queued"` and commits async
   (durable queue, §15) *plus* the ArangoSearch view commits on a 1 s interval
   (`schema/collections.py` → `commitIntervalMsec: 1000`). At a pipeline handoff, agent B
   can silently miss agent A's final writes.
2. **Cross-agent visibility is a convention, not a capability** — every retrieval arm
   hard-filters `doc.agent_id == @agent_id` (`retrieve/search.py`). Sharing works only by
   writing to a pseudo-agent namespace (`<crew_id>::query`, §14); reading "mine + shared"
   takes N separate calls with no cross-tier fusion.
3. **No handoff primitive** — "brief me for this task" doesn't exist; B must invent a
   retrieval query. Agent *outputs* aren't captured either (the Vercel middleware stores
   only the last user text), so A's conclusions never reach the brain unless they pass
   through tool calls.

Each item below is a self-contained work package (branch → PR → squash-merge, keyless CI
green) with scope, files, tests, and acceptance criteria. IDs are `MA-*` (multi-agent).
Sizes: S ≈ ≤1 day, M ≈ 2–3 days.

## Priority order

Status: ✅ shipped · 🔄 built, merge pending · ✖ closed (won't do). Unmarked = open.

| # | ID | Item | Size | Depends on |
|---|----|------|------|-----------|
| 1 | MA-1 | Read-your-writes: `sync` store + `/v1/flush` barrier ✅ | S | — |
| 2 | MA-2 | Multi-`agent_id` retrieval with single-pass fusion ✅ | M | — |
| 3 | MA-3 | `POST /v1/prime` — task briefing endpoint ✅ | M | MA-2 |
| 4 | MA-4 | Capture assistant outputs in the Vercel middleware ✅ | S | — |
| 5 | MA-5 | Handoff eval (A writes → B retrieves, scored) ✅ | M | MA-1, MA-2 |
| 6 | MA-6 | Docs: multi-agent orchestration guide ✅ | S | MA-1..3 |
| 7 | MA-7 | Per-agent key binding + insight-tier write protection ✅ | S | — |
| 8 | MA-8 | Vector-index reliability + resume P1 benchmark ✅ (infra; the run itself is user-executed) | M | — |
| 9 | RQ-1 | Multi-hop query decomposition / iterative retrieval ✅ — opt-in, benchmark-dependent (helps on MuSiQue) | L | — |
| 10 | BX-1 | Benchmark expansion: MuSiQue multi-evidence dataset + metric ✅ | M | — |
| 11 | RQ-2 | Close the retrieval-content gap (diagnostic → cross-encoder reranker) ✅ — +0.135 recall | L | — |
| 12 | BX-2 | Pooled-corpus MuSiQue (open-retrieval variant, tests first-stage recall) ✅ — surfaced the scalability wall → BX-3, SC-1 | S | BX-1 |
| 13 | BX-3 | Lightweight pooled diagnostic (extract-skip + graph-off; routes around O(n²) wall) ✅ — 33% first-stage gap | S | BX-2 |
| 14 | SC-1 | Single-large-tenant scalability: ANN entity resolution + bounded graph fan-out ✅ (SC-1a–d) | L | — |
| 15 | RT-1 | Expose `candidate_pool` as a config + API knob (open-corpus tuning) ✅ | S | — |
| 16 | RQ-3 | Rerank scoring: replace vs rank-blend vs event-time-aware (paired LongMemEval) ✅ — **replace stays** | M | RQ-2b, IN-4 |
| 17 | CW-1 | Port the CrewAI shim to crewai ≥ 1.10 unified memory ✅ — hybrid backend + paired embedder | M | — |
| 18 | MCP-1 | MCP OAuth authorization flow 🔄 — built in #262, merging after the v0.1.0 tag | M | #255 |
| 19 | MCP-2 | Packaged MCP HTTP deployment ✅ — one image runs core or MCP; recipes; hosted service not deployed | S | #255 |
| 20 | MCP-3 | Legacy HTTP+SSE transport — ✖ closed, won't do (no client needs it) | S | #255 |
| 21 | IMG-1 | Move the image to a newer Python ✅ — 3.14; Grype exception removed | S | — |
| 22 | OBS | Obsidian adapter: vault ingestion, plugin, example vault (OBS-1–3) | L | — |
| 23 | GX-1 | Entity summaries in `/v1/retrieve` (Graphiti's ENTITIES section) 🔄 — built in #266, off by default; **benchmark deferred** | S | — |
| 24 | GX-2 | Memory-level fact supersession + `as_of` retrieval (the scoped newness signal RQ-3 called for) 🔄 — built, off by default | L | — |
| 25 | GX-3 | Fact retrieval arm over typed-relation statements (needs an LLM/GLiREL extractor) | L | GX-2 |
| 26 | LM-1 | Neighbour expansion: inject the turns next to each hit (`opts.neighbor_turns`) 🔄 — built, off by default; answer accuracy not yet measured | S | — |
| 27 | FK-1 | Fact-augmented keys: Dream-extracted facts per memory, matched by two extra retrieval arms 🔄 — built, off by default; measured, no accuracy gain | M | — |
| 28 | WQ-1 | Write queue: more than one worker thread, so a burst of async writes drains in seconds, not minutes | M | — |
| 29 | PF-1 | Request latency: handlers off the event loop, no embeddings in arm results, cached vector-index state, batch writes on, fewer round trips (PF-1a–h) | L | WQ-1 |

Recommended sequence: **MA-1 → MA-2 → MA-3 → MA-4 → MA-5 → MA-6**, with MA-7/MA-8
schedulable any time (no dependencies on the others). MA-1…MA-8 are **shipped**. **RQ-1**
(multi-hop retrieval) is also shipped as opt-in `mode="multihop"`; its value is
**benchmark-dependent** — neutral/harmful on LoCoMo (single-turn gold) but **+0.165 all-hops
recall on MuSiQue** (genuinely multi-evidence; see the RQ-1 outcome note below and DESIGN §23).
**BX-1 is shipped** (multi-evidence metric + MuSiQue converter), which enabled that re-trial.
**RQ-2 is shipped:** the diagnostic found MuSiQue misses are 100% ranking-bound, and the
cross-encoder reranker (`rerank=true`) it pointed to lifted all-hops recall **0.430 → 0.565**
on MuSiQue. Rerank and multihop are different, composable levers and they **stack
super-additively — `MODE=multihop RERANK=--rerank` reaches 0.810 all-hops / 0.905 recall-frac**
(the recommended max-recall config; DESIGN §23). No further scheduled item.

**Companion:** [GUILD.md](GUILD.md) redesigns the `examples/dungeon` demo around this
work — expendable heroes, a torch-as-context-window budget, and a Handoff Briefing
screen that renders `/v1/prime` live. Its E-1/E-3/E-4 packages run on today's core;
E-2 is the visible payoff of MA-1 + MA-2 + MA-3.

---

## Ingestion scalability (`IN-*`) — TOP PRIORITY

**Where this comes from:** the LongMemEval run (2026-08, DESIGN §23) forced `extract=False`
to terminate — which meant the benchmark measured only the BM25+vector **substrate**, with the
entity graph, supersession, belief, and communities all switched **off**. We benchmarked the
substrate, not the product, and the reason is architectural: **`store()` is O(round trips) per
turn, quadratic in entities-per-turn**, and there is no bulk path.

**The arithmetic (counted from the code).** One `store(extract=True)` issues
`4 + 4E + E(E−1)/2` sequential DB round trips (E = entities/turn): `episodes.has` +
`episodes.insert` + `memories.insert` + an **uncached** `collection.indexes()` probe
(`ensure_vector_index` → `has_vector_index`), then **per entity** an ANN query + an upsert +
2 edge inserts, then **per co-occurring pair** a `relates_to` upsert. At E=5 that's **34 round
trips/turn → ~17,000/question** over a 500-turn history → ~7 min/question at ~25 ms/hop —
exactly what was observed. `extract=False` drops it to 3/turn (~40× faster), which is why the
substrate run was tractable. SC-1b/c/d fixed the *tenant-size* O(N²); this is the per-turn
**constant** and the per-turn **O(E²)** that remain.

**The design: two-phase ingestion (batch, don't drop).** Split *recording* from *reflecting*.
Every capability is preserved — only *when* and *in what batch* the work runs changes.
- **Phase 1 "record" (hot):** redact → batch-embed → **bulk-insert** episodes + memories.
  ~2 round trips per *batch*. BM25+vector retrieval works immediately (MA-1 unaffected).
- **Phase 2 "reflect" (batched graph pass):** batched extraction → one embed batch of distinct
  names → one ANN query per distinct name (batch-local resolution map) → **aggregate** mentions
  per entity → one bulk entity upsert → one bulk edge insert per collection. ~a handful of round
  trips per batch instead of thousands.

**Why batching is provably safe (load-bearing).** Belief is
`confidence × (1 − (1−base)^Σreliability)` — a function of the **sum** of reliabilities
(`entities.py`), and corroboration counts are additive. Folding N mentions into one upsert with
`Σrel` is **mathematically identical** to N sequential upserts. The only non-associative term is
the EWA edge weight recurrence; within a batch Δt≈0 and it's a recency heuristic, not a
correctness invariant — fold once per batch and document the approximation.

**The bonus insight.** `RRF_GRAPH_WEIGHT` is pinned at 0.1 because the graph "buries real hits"
— but that's because linking *every* entity pair in *every* turn (O(E²)) fills the graph with
meaningless `associated_with` edges, so hop-distance carries no signal. Capping the fan-out
(IN-3) fixes speed **and** graph quality together, and plausibly lets the arm's weight rise —
which is exactly what `multi-session` recall (0.067 on LongMemEval) needs.

| # | ID | Item | Effect | Size |
|---|----|------|--------|------|
| 1 | IN-1 | `store_many()` bulk record path (Phase 1) ✅ | ~40× ingest; unblocks honest benchmarking | M |
| 2 | IN-2 | Batched graph pass (Phase 2) + memoized index probe ✅ | graph ON at benchmark scale | M |
| 3 | IN-3 | Cap co-occurrence fan-out ✅ (`graph_max_pairs_per_turn`) | speed **+ graph quality** (revisit `RRF_GRAPH_WEIGHT`) | S |
| 4 | IN-4 | Metadata-as-fields (dates) surfaced at assembly ✅ | recovers the date-noise recall regression | S |
| 5 | IN-5 | Re-run stratified LongMemEval with the graph ON | harness ✅; substrate-only **0.411** recorded; graph-on run confounded by `fake` extractor → **HX-1b** | — |
| 6 | HX-1b | Legitimate graph-on LongMemEval (real extractor + reranker) ✅ | **0.522 vs 0.411 substrate (+0.111)**; multi-session 0.067→0.267; every type up | S |
| 7 | IN-6 | Batch entity resolution (the read loop IN-2 left unbatched) ✅ | graph-on ingest minutes→seconds; unblocks haiku rung + prod graph | M |
| 8 | HX-1c | Isolate graph vs reranker (graph-OFF + real reranker run) ✅ | split the +0.111: **graph +0.089, reranker +0.022** (0.411→0.433→0.522) | S |
| 9 | IN-7 | Concurrent per-memory extraction (thread pool) ✅ | LLM-extraction wall gone: haiku ingest overnight→~1h; prod-viable | S |
| 10 | HX-1d | The haiku extractor rung (does extractor quality beat spaCy 0.522?) ✅ | **haiku 0.589 vs spaCy 0.522 (+0.067)**; verdict: LLM extraction pays on accuracy, spaCy stays the default (~$45/1h vs keyless secs) | S |

**Recommended sequence: IN-1 → IN-3 → IN-2 → IN-4 → IN-5 → HX-1b.** IN-1 removes the reason
`extract=False` exists; IN-3 is small and lifts graph quality; IN-2 makes the graph affordable
at scale; IN-4 fixes the date-encoding regression; IN-5 wired + ran it (substrate-only 0.411 recorded,
but the graph-on run was confounded by the `fake` extractor); **HX-1b** produces the real graph-on
number worth publishing, with a real extractor + reranker.

### IN-1 — `store_many()` bulk record path

**Problem.** `store()` is the only write path; every caller ingests one item at a time
(`grep`: zero `insert_many`/`import_bulk` in the core). Even with `extract=False`, a 500-turn
history is ~1,500 sequential round trips + N sequential embedding calls (the pre-warm mitigates
the latter but not the writes).

**Design.** Add `store_many(db, items, *, extract=False, ...)` that, for a list of turns:
redacts all, computes idempotency keys, **batch-embeds** (reuse `embed_batch_cached`), and
**bulk-inserts** episodes + memories via `collection.insert_many(overwrite_mode="ignore")` (or
an AQL `FOR … INSERT`). Returns per-item `StoreResult`s. `store()` stays as the single-item
convenience wrapper (`store_many([one])`) so adapters/interactive turns are unchanged.
Idempotency + read-your-writes semantics preserved (same keys, same view-sync). Working-memory
capacity + topic-shift tracking apply per session as today (batch may span sessions — group by
session for those side effects). `extract` handling deferred to IN-2 (Phase 2); IN-1 lands the
record path with `extract=False`.

**Files.** `core/src/arango_memory/ingest/store.py` (new `store_many`, factor `_store_impl`'s
episode/memory doc-builders for reuse), `core/src/arango_memory/ingest/bulk.py` (optional
helper), tests, `docs/api.md`/DESIGN §8.

**Tests.** `store_many` of N turns issues O(1) insert round trips (assert via a call-counting
fake db or by wall-clock/`explain`), not O(N); results equal N× `store()` (same keys, same
retrieval); idempotent replay; mixed sessions handled; keyless (FakeEmbedder), testcontainers.

**Acceptance.** A benchmark harness ingesting a 500-turn history via `store_many` completes in
seconds, not minutes; single-item `store()` behavior byte-identical; CI green.

### IN-2 — Batched graph pass (Phase 2)

**Design.** `write_entities_many(db, memories, ...)`: extract per memory, collect **distinct**
entity names across the batch, one embed batch, one ANN query per distinct name into a
batch-local `name→key` resolution map (repeated names free), aggregate mentions/reliabilities
per resolved entity, then **one bulk upsert** (AQL `FOR e IN @rows UPSERT …`) and **one bulk
edge insert** per collection (`mentions`/`produced_by`/`relates_to`). Belief/corroboration fold
by sum (identical result). Memoize `has_vector_index` per (db, collection). Wire into
`store_many(extract=True)` and the durable worker (batch-drain). Cold-start (untrained ANN)
falls back to the existing scan, once per batch.

**Files.** `ingest/entities.py` (batched path), `ingest/store.py`, `schema/collections.py`
(memoized probe), `ingest/worker.py` (batch drain), tests, DESIGN §8.

**Acceptance.** `store_many(extract=True)` over a 500-turn history completes in seconds with an
entity graph identical (belief/counts/edges) to the per-turn path; round trips per batch are
O(distinct entities), not O(Σ pairs).

### IN-3 — Cap co-occurrence fan-out

**Design.** `cooccurring_pairs` currently emits all `E(E−1)/2` pairs. Bound it: a
`GRAPH_MAX_PAIRS_PER_TURN` cap (config), and/or scope pairs to a sentence/window, and/or prefer
typed relations and only backfill co-occurrence up to the cap. Fewer edges → faster writes **and**
a higher-signal graph; then re-measure `RRF_GRAPH_WEIGHT` on LoCoMo/MuSiQue (it may rise off 0.1).

**Files.** `ingest/extract.py` (`cooccurring_pairs`), `config.py`, `ingest/entities.py`, tests,
`docs/ops.md` knob, DESIGN §5/§9.

**Acceptance.** Pairs/turn bounded by the cap; graph-arm recall on LoCoMo/MuSiQue does not
regress at weight 0.1 and is re-tested at a higher weight; documented.

### IN-4 — Metadata-as-fields at assembly

**Problem.** Prefixing dates into every turn's matchable text (LongMemEval date fix) diluted BM25
and cost single-session-user recall (0.733 → 0.467) while lifting temporal (0.067 → 0.533).
**Design.** Carry provenance/time as memory **fields** (e.g. `event_time`, `source_date`) rather
than in `text`; surface them in the assembled context (a `[date]` prefix at *assembly*, not at
*index*), so retrieval matches clean content but the answerer still sees the timestamp. The eval
converter sets the field instead of mangling the text.
**Files.** `retrieve/search.py` (assembly), `ingest/store.py` (field passthrough),
`eval/longmemeval_convert.py`, tests, DESIGN §9.
**Acceptance.** Temporal keeps its gain; single-session-user recovers; dates visible in context.

### IN-5 — Re-run LongMemEval with the graph ON  ✅ (harness) / ⚠️ result confounded

After IN-1..4, re-run the stratified-90 with `extract=True` (now affordable) and record the
per-type table in DESIGN §23 + a README callout — the number that reflects the *product*, not
the substrate. Compare graph-on vs graph-off to quantify what supersession/entities buy on
`knowledge-update` and `multi-session`.

**Outcome (2026-08-13).** Harness wiring shipped (`store_many(extract=True)`, batched graph pass).
The substrate-only number is recorded: **0.411** overall (DESIGN §23). The graph-**on** run,
however, is **not** a valid measurement: `EXTRACTION_PROVIDER` defaulted to `fake`, so `--extract`
built the graph with the FakeExtractor (every capitalized span → a `Concept` node). That junk graph
*lowered* accuracy to 0.367 — a confound, not the product number. The real graph-on test moves to
**HX-1b** (needs a real extractor). IN-5's affordability + wiring goal is done; the measurement isn't.

### HX-1b — Legitimate graph-on LongMemEval (real extractor + real reranker)  ✅

**Result (2026-08-14, DESIGN §23).** Graph ON (`EXTRACTION_PROVIDER=spacy`) + real reranker
(`RERANKER_PROVIDER=local`), stratified-90: **0.522 overall vs 0.411 substrate (+0.111, +27% rel)**.
**Every question-type improved, none regressed**; `multi-session` — the graph's whole reason for being —
went 0.067 → 0.267 (~4×). The product configuration earns its keep on a standard LTM benchmark. Caveat:
the run flips two switches (graph + real reranker) vs the substrate baseline, so +0.111 is the combined
product gain; a fast graph-OFF + real-reranker isolation run (no `--extract`) to split them is the open
follow-up.

**Why.** The graph-on number is the one that reflects the *product* (entities + supersession +
belief), but it can only be measured with a **real** extractor. The IN-5 run used the default
`fake` extractor and is meaningless; the substrate-only 0.411 is the current honest headline.

**Plan.**
1. Set a real `EXTRACTION_PROVIDER`. Recommended ladder: **`spacy`** first (local, ~ms/turn, real
   typed NER — cheap, needs the `extraction` extra), then **`haiku`** for the headline if spacy shows
   lift (LLM extraction is per-memory: ~550×90 ≈ 50k calls → slow + real $). Note the batched graph
   pass (IN-2) bulk-ed the *DB round-trips*, not the LLM extraction calls — so haiku is still costly.
2. Also set `RERANKER_PROVIDER=local` (+ the `rerank` extra) so `--rerank` is real — it was a no-op
   `fake` reranker in the IN-5 run, so neither recorded number reflects the reranker.
3. Re-run the stratified-90 (`--mode lite --k 10 --rerank --extract`) and record graph-on vs the
   0.411 graph-off baseline in DESIGN §23 + a README callout.

**Run (spacy rung — wired).**
```bash
cd core
# 1. install the lightweight spacy-only extractor extra + its NER model (one-time)
uv sync --extra dev --extra extraction-spacy --extra rerank
make spacy-model                       # python -m spacy download en_core_web_sm
# 2. real providers for the graph + the real reranker (in core/.env or the shell)
#    EXTRACTION_PROVIDER=spacy   RERANKER_PROVIDER=local
#    EMBEDDING_PROVIDER=openai   GENERATION_PROVIDER=anthropic   (+ keys)
# 3. convert the stratified-90 (skip if lme.json already exists) and run graph-ON
uv run --no-sync python -m arango_memory.eval.longmemeval_convert longmemeval_s.json lme.json --stratified --limit 90
make longmemeval LME_DATASET=lme.json MODE=lite K=10 RERANK=--rerank EXTRACT=--extract
```
The `haiku` rung is the same run with `EXTRACTION_PROVIDER=haiku` (no model download; costs $ + time).

**Watch.** `multi-session` (0.067) and `knowledge-update` (0.533) — cross-session fact-linking and
supersession are exactly what the entity graph is meant to buy; `multi-session` is the biggest gap
and the clearest signal of whether the graph earns its cost here.

**Success.** A recorded, un-confounded graph-on table + a one-line verdict: does the real entity
graph raise LongMemEval accuracy over the fusion substrate, and on which question-types.

### HX-1c — Isolate graph vs reranker (attribution run)  ✅

> **Update (2026-11-19, DESIGN §23 rev 108):** the graph's +0.089 did not reproduce. With the graph
> arm off, evidence recall@20 is identical on every question of both held-out sets (0.941, 0.938).
> Treat the result below as historical. On MuSiQue (rev 109) the arm is a non-significant +0.02
> recall without the reranker and identical with it; an open-corpus test is the one that could
> still show a gain.

**Result (2026-08-14, DESIGN §23).** One graph-OFF + real-reranker run splits the +0.111:
substrate 0.411 → **+reranker 0.433** → **+graph 0.522**. **The graph is the dominant lever: +0.089
(~80%) is the entity graph, +0.022 (~20%) is the reranker** — the differentiator does the heavy lifting,
not the off-the-shelf cross-encoder. (Overall split is robust; per-type n=15 deltas are noisy.)

**Why.** The HX-1b product number (0.522 vs 0.411, +0.111) flips **two** switches at once — the
entity graph *and* a real reranker (the substrate baseline's `--rerank` was a no-op `fake`). So the
+0.111 is the combined product gain; we can't yet say how much is the graph vs the cross-encoder.

**Plan — one cheap run.** Graph **OFF** + real reranker: `RERANKER_PROVIDER=local`, `--rerank`, **no**
`--extract`. No extraction means substrate-speed ingestion (minutes, not hours — and IN-6 makes even
the graph-on rung fast now), so this is a quick add. Same stratified-90 slice/model.
- reranker-only Δ = (this run) − 0.411 substrate.
- graph-only Δ ≈ 0.522 product − (this run).
Record both deltas in DESIGN §23 next to the HX-1b table.

**Success.** A three-row picture — substrate / +reranker / +reranker+graph — that attributes the
+0.111 across the two levers, closing the one open caveat on the HX-1b result.

### IN-7 — Concurrent per-memory extraction  ✅

**Done (2026-08-14).** The batched graph pass (IN-2) bulk-ed the DB round trips, but per-memory
**extraction** still ran in a sequential loop — the wall for an LLM extractor (`haiku` = one LLM
call per turn → ~O(turns) blocking calls per question). Extraction is independent per memory, so it
now runs under a bounded thread pool (`ThreadPoolExecutor`, `EXTRACTION_CONCURRENCY`, default 8);
`map` preserves order so the downstream accumulation is byte-identical (the IN-2 equality property
holds — verified by `test_write_entities_many` in CI). `HaikuExtractor`'s cache upgraded to a
thread-safe per-text dict so concurrent `extract`/`extract_relations` can't clobber and double the
(paid) LLM calls (`test_extract.py`). Collapses wall-time for LLM tiers — turns the HX-1d haiku run
from an overnight job into ~an hour, and makes `haiku` viable in production, not just benchmarks.

**Follow-up (2026-09): the pool is I/O-bound extractors only.** The original note claimed the pool
was "neutral" for CPU-bound tiers; it isn't. spaCy's `nlp()` holds the GIL, so 8 threads convoy on
it: one 413-turn LongMemEval knowledge-update question (`EXTRACTION_PROVIDER=spacy`, graph on, real
openai embeddings + local bge-reranker, macOS) took **2m14s at `EXTRACTION_CONCURRENCY=8`** (≈356s
*system* CPU, 300% cpu) vs **42s at 1** (≈2s system), identical outputs; capping BLAS/OpenMP threads
didn't help, so it isn't BLAS oversubscription. Extractors now declare `io_bound` (True for
`HaikuExtractor`, and for `LayeredExtractor` when a tier may escalate to Haiku; False for
fake/spaCy/GLiNER) and `write_entities_many` threads only when it's True. A third-party extractor
without the attribute is treated as I/O-bound — the pre-flag behavior, and custom extractors are
most often API wrappers; a CPU-bound one should set `io_bound = False`.

**Confirmed after merge (#241, 2026-09-24).** Same question (`6a1eabeb`), same config
(`--k 10 --rerank --extract`), fresh DB per run:

| code | `EXTRACTION_CONCURRENCY` | wall | system CPU |
|---|---|---|---|
| pre-fix | 8 | 94s / 101s (2 runs) | 140s / 168s |
| pre-fix | 1 | 58s | 3.0s |
| **post-fix** | **8** | **49s** | **2.4s** |

The default now matches the sequential path (the setting is inert for spaCy), and the graph
matches across all four runs (1,287 entities, 2,891 mentions, 3,595 `relates_to`,
Σcorroboration 5,200 — same entity checksum). Absolute times sit below the original
42s/2m14s (machine/network load); the convoy signature — system CPU ≫ user at 8 threads,
≈0 sequentially — is the same.

**Files.** `core/src/arango_memory/ingest/entities.py` (pooled step 1),
`core/src/arango_memory/ingest/extract.py` (thread-safe Haiku cache), `core/src/arango_memory/config.py`
(`extraction_concurrency`), `docs/ops.md`.

### Examples — concurrency review  ✅ (complete)

**Done (2026-08).** Swept every example for the app-level version of the IN-6/IN-7 lesson
(independent per-item core calls run in a sequential loop). The core-side batching (`store_many`)
isn't reachable from the examples — they all write via single `/v1/store` — so the lever is
client-side parallelism/batching. Outcome per example:

- **`examples/diligence-room`** ✅ changed (#216) — per-document claim extraction within a specialist
  now runs under a bounded, order-preserving `mapLimit` (`DILIGENCE_EXTRACT_CONCURRENCY`).
- **`examples/dungeon`** ✅ changed (#221) — the `talk` tool's per-claim writes are parallelized and
  the entity seeds batched into one `/v1/seed` (~2N core hops → ~2).
- **`examples/vercel-agent`** — reviewed, **left unchanged**: a minimal single-turn reference with no
  per-item fan-out; its two demo turns are sequential by design (turn 2 recalls turn 1).
- **`examples/mcp-memory`** — reviewed, **left sequential**: parallelizing per-tool calls would
  misrepresent how MCP hosts drive tools (one at a time per turn) and gain little (flush-dominated).

No further work scheduled; the sweep is closed. (All four outcomes recorded in CHANGELOG.)

### HX-1d — The haiku extractor rung (does extractor quality beat spaCy's 0.522?)  ✅ (#232)

**Done (2026-09-11).** Result recorded in DESIGN §23 (rev 91): **haiku 0.589 vs spaCy 0.522 —
+0.067 (+13% relative).** Six of seven types up; only `single-session-user` down one question
(0.600→0.533, within the n=15 ±1–2-Q noise band). Biggest lifts: `single-session-preference` doubled
(0.133→0.267), `single-session-assistant` 0.867→1.000. Config: fresh `ARANGO_DB=hx1d_haiku`,
`RERANKER_PROVIDER=local`, `EXTRACTION_CONCURRENCY=16`, `--k 10 --rerank --extract`,
`EXTRACTION_PROVIDER=haiku` (`background_model=claude-haiku-4-5`); ~$45 / ~1h wall. The run also
exercised the #231 fail-soft path — rode through a transient Anthropic 5xx to 90/90 (an earlier
attempt crashed at 38/90 on an unhandled 500).

**Verdict:** extractor quality pays on accuracy, but **spaCy stays the default** — keyless and seconds
vs haiku's ~$45/1h. `haiku` is the accuracy-max option, documented as such.

**Why (hypothesis, confirmed).** spaCy gave the graph +0.089 (→ 0.522). `haiku` extraction is richer —
**typed entities + typed relations** (spaCy emits none, falling back to co-occurrence) + better entity
recall — so a higher-quality graph lifts the graph-sensitive types further. IN-7's concurrency was the
prerequisite (one LLM call per memory, ~49,500 on stratified-90 → ~1h instead of ~10–20h sequential).

### IN-6 — Batch entity resolution (the unbatched read loop IN-2 left behind)  ✅ (#208)

**Done (2026-08-14).** Shipped in [#208](https://github.com/mikefons/arango-agentic-memory/pull/208):
resolution now folds into the batch — one candidate-pool fetch (`_resolution_pool`: a single
batched-ANN AQL over all query vectors, or one full scan) + one numpy matmul (`_best_match_many`)
for the whole batch, replacing the per-entity ANN query / O(cardinality²) Python cosine. Belief
folds by sum so batched == per-item (proven by the existing `test_write_entities_many` equality
harness in CI, plus a new pure-function `test_best_match_many`). numpy promoted to a core dep.
Speeds up *future* graph-on runs only — an in-flight run on the old code is unaffected.

**Problem — found during the HX-1b spacy run.** With a real extractor, graph-on ingestion is back
near the pre-IN wall: **~7.5 min/question** (≈11 h for the stratified-90, local ArangoDB, CPU-bound).
IN-1/IN-2 batched the graph *writes* (bulk upsert/insert/relate) but **not** the entity *resolution
reads*. In `write_entities_many` (`ingest/entities.py` step 3, ~L511) resolution is still **one lookup
per distinct entity**:
- **ANN path:** a `_NEAREST_ENTITIES` AQL query is issued *inside the loop*, once per distinct entity
  → N round trips per batch.
- **Scan path:** `_best_match` cosines each new entity against *every* existing entity in Python
  → O(N·M) per batch.

Both scale with **entity cardinality**, which is exactly what a real extractor explodes: the `fake`
extractor emits few distinct entities (capitalized spans, heavy repeats) so this loop was invisible;
spaCy emits hundreds–thousands per question, so it dominates. This is why the fake-graph run was fast
and the real-graph run is not — the cost was hiding in the read loop, not the writes.

**Design.**
1. **Batch the ANN resolution into one round trip.** Replace the per-entity `_candidates()` calls with
   a single AQL that takes the whole array of query vectors and returns top-k per vector (`FOR qvec IN
   @qvecs …`), so N entities cost 1 request, not N.
2. **Vectorize the scan path.** Fetch the tenant's existing entity matrix once, then a single
   `numpy` matmul (N×D · D×M) + per-row argmax to pick best matches — replacing the Python triple loop.
   Falls back cleanly at small M. (numpy is already a transitive dep via faiss/embeddings.)
3. **Dedup names before embedding** (L468 flattens *every* mention; embed the distinct set — the cache
   masks most of it but not all).
4. Keep the math identical: resolution only *chooses* merge targets; belief/corroboration still fold by
   sum (the IN-2 equality property), so batched-resolve == per-entity-resolve. Reuse the
   `test_write_entities_many` equality harness to prove it.

**Files.** `core/src/arango_memory/ingest/entities.py` (step 3 + the `_NEAREST_ENTITIES` AQL),
`core/tests/test_write_entities_many.py` (equality still holds + a cardinality/perf smoke),
DESIGN §8/§23 (note the resolution batching alongside IN-2).

**Success.** Graph-on ingestion drops from ~minutes/question to seconds; the HX-1b spacy run finishes
in well under an hour; `test_write_entities_many` still shows batched == per-item. Unblocks the `haiku`
rung (the LLM-extraction cost stays, but the resolution wall is gone) and makes graph-on viable in
production, not just benchmarks.

---

## MA-1 — Read-your-writes: `sync` store + `/v1/flush` barrier

**Problem.** `POST /v1/store` enqueues a durable write intent and returns `"queued"`;
a worker commits it later, and BM25 visibility lags a further ≤1 s behind the commit
(ArangoSearch `commitIntervalMsec: 1000`). An orchestrator that runs
`A.store(...) → B.retrieve(...)` gets intermittent missing context — a correctness hole
at exactly the handoff moment.

**Design.**
- `StoreRequest` gains `sync: bool = False` (same on `/v1/step`). When `sync=true`, the
  handler processes the intent **inline** (same code path the worker runs — reuse, don't
  fork logic), then forces search-view visibility before responding. Response `status`
  becomes `"committed"` (vs `"queued"`).
- View visibility: after commit, either issue the AQL no-op that forces a view sync
  (query the view with `OPTIONS { waitForSync: true }`) or poll for the written doc key in
  the view with a short deadline (~2 s). Prefer the `waitForSync` view option — measure
  both against the §23 latency budget and document the choice.
- `POST /v1/flush` (barrier): body `{ ctx }`. Blocks until the queue has **no pending or
  claimed intents for that tenant** and the view is synced, with a `timeout_ms` (default
  5000) → `{"status": "flushed"}` or `{"status": "timeout", "pending": n}` (HTTP 200 both
  ways — callers branch on status; a timeout is not a server error).
  - Queue support: extend the `WriteQueue` Protocol (`ingest/queue.py`) with
    `pending_count(tenant_id) -> int`. Trivial for `InProcessQueue`; a filtered COUNT for
    `ArangoQueue`.
- Adapters: expose `sync` on the TS client + `flush()` helpers in `lib`-level clients
  (Vercel package option `syncWrites?: boolean`; MCP gains a `flush` tool; LangChain /
  CrewAI pass-through kwargs).

**Files.** `core/src/arango_memory/api/app.py` (models + handlers),
`core/src/arango_memory/ingest/queue.py` (+ both backends),
`core/src/arango_memory/ingest/` (worker's commit fn factored for inline reuse),
`packages/vercel/src/index.ts`, `core/src/arango_memory/mcp/tools.py`, docs (`api.md`,
DESIGN §15/§19).

**Tests.** `sync=true` → store then immediate BM25 retrieve finds the text (no sleep);
`flush` returns only after a queued intent lands (enqueue N, flush, assert visible);
flush timeout path (stall the worker with a fake queue); idempotency preserved under
sync (replay same idempotency_key). All keyless (FakeEmbedder), testcontainers.

**Acceptance.** A `store(sync=true) → retrieve` round-trip in one test with **zero
sleeps** passes deterministically; `flush` documented in `api.md`; §15 updated to state
the consistency model explicitly (async by default, opt-in barrier).

**Out of scope.** Cross-instance flush fan-out (single-instance semantics documented;
the Arango-backed queue makes multi-instance flush *mostly* correct already — note the
caveat, don't solve it).

---

## MA-2 — Multi-`agent_id` retrieval with single-pass fusion

**Problem.** All three retrieval arms filter to exactly one `agent_id`
(`retrieve/search.py:46,60,81`). "My private memory + crew shared + crew insights"
requires three HTTP calls and client-side merging that bypasses the core's RRF fusion,
decay, belief, and salience boosts.

**Design.**
- `AccessContext` gains `read_agent_ids: list[str] | None = None` (default `None` →
  current single-`agent_id` behavior; nothing changes for existing callers). Writes are
  untouched — `agent_id` remains the sole write identity.
- AQL: `doc.agent_id == @agent_id` → `doc.agent_id IN @agent_ids` in all three arms
  (vector, BM25, graph). Bind the list once; keep tenant filter untouched (isolation is
  tenant-level and already tested — this must not loosen it).
- Fusion is automatically cross-tier once candidates flow from one query — no fusion code
  change expected. Add `agent_id` (provenance) to `MemoryHit` so consumers can weight or
  display "who wrote this".
- Authz: in enforced mode, every id in `read_agent_ids` must still belong to the key's
  tenant (already guaranteed by the tenant filter — assert it in a test, not new code).
  Per-agent restrictions are MA-7, not here.
- CrewAI storage (`crewai/storage.py`): teach `search()` to pass
  `read_agent_ids=[own, crew::query, crew::insight]` in **one** call instead of per-tier
  lookups; keep the tier objects for writes.

**Files.** `core/src/arango_memory/api/app.py` (`AccessContext`, `MemoryHit`),
`core/src/arango_memory/retrieve/search.py` (all arms + scope dict),
`core/src/arango_memory/crewai/storage.py`, `packages/vercel/src/index.ts`
(`readAgentIds?: string[]` option), MCP `search` tool arg, `api.md`, DESIGN §14
(upgrade "schema-ready" → "first-class").

**Tests.** Two agents write distinct facts; retrieve with `read_agent_ids=[a, b]` returns
both, ranked by one fusion pass; provenance field correct; default (`None`) returns only
own memories (regression); cross-**tenant** id in the list yields nothing (isolation
holds); CrewAI one-call path returns tiered results.

**Acceptance.** One `retrieve` call spans N namespaces with fused ranking + provenance;
existing single-agent tests unmodified and green.

**Out of scope.** A `visibility` field on memories (private/crew/tenant enum) — the
namespacing convention + explicit id lists cover current consumers; revisit if a real
consumer needs doc-level ACLs.

**Future enhancement — cross-agent corroboration display.** When several agents wrote
the *same* fact, MMR de-dupes them by embedding similarity and the survivor carries only
one agent's provenance — so a briefing can't show "both A and B knew this." Correct for
ranking, but a future pass could surface corroborating authors (a `also_known_by: [...]`
on a hit) when multiple agents independently recorded a fact. Deferred (agreed): not
needed for the handoff use case; MMR collapsing duplicates is the right default for now.

---

## MA-3 — `POST /v1/prime`: task briefing endpoint

**Problem.** The handoff verb is missing. Agent B (or the orchestrator) should say
"here's the task, brief me" and get one budgeted context package — not hand-craft a
query and hope. (This absorbs the "prime endpoint" idea from the beads comparison in
`future-investigations`.)

**Design.**
- `POST /v1/prime` body: `{ task: str, ctx, opts? }` where `opts` adds
  `include: {episodic: bool, semantic: bool, procedural: bool}` (all true) on top of the
  usual `mode`/`k`/`max_memory_tokens`. `ctx` supports `read_agent_ids` (MA-2).
- Composition (no new retrieval machinery):
  1. **Episodic/semantic hits** — existing `retrieve(task, ...)` pipeline as-is.
  2. **Entities** — top-N semantic entities among the hits' mentions (existing
     projections; include belief/salience fields already surfaced by CC/SAL work).
  3. **Procedural** — match `steps` against the task text (BM25 over tool
    names/args/outcomes; reuse the steps read path from `/v1/steps`), so B sees *how*
    similar jobs were done, including failures.
  4. **Assembly** — one markdown-ish briefing string (mirrors `retrieve`'s `context`
     contract) with sections `## Relevant history`, `## Key entities`,
     `## Prior tool runs`, packed under `max_memory_tokens` with a fixed section budget
     split (e.g. 50/25/25, truncate lowest-scored first). Also return the structured
     parts (`hits`, `entities`, `steps`) so richer consumers can self-assemble.
- Response: `{ context: str, hits: [...], entities: [...], steps: [...],
  tokens_injected: int }`.
- Adapters: MCP tool `prime` (the flagship consumer — any MCP harness gets handoff
  for free); TS client `prime()`; LangGraph `prime` node variant of `recall`.

**Files.** `core/src/arango_memory/api/app.py`, new
`core/src/arango_memory/retrieve/prime.py` (assembly logic, unit-testable pure
functions), `mcp/tools.py` + `mcp/server.py`, `packages/vercel/src/index.ts` (exported
helper, not middleware), `api.md`, DESIGN §19 (add to the contract table).

**Tests.** Briefing contains all three sections when data exists; token budget respected
(inject oversized corpus, assert ≤ budget); section omitted cleanly when `include` flag
off or no data; procedural matching finds a step by tool name; works with
`read_agent_ids` spanning a writer agent and a reader agent (the actual handoff shape);
MCP tool contract test.

**Acceptance.** The scenario in this doc's header is demo-able with two curl calls:
`store` as agent A (sync), `prime` as agent B with `read_agent_ids=[A, shared]` — B's
briefing contains A's facts and tool history.

**Out of scope.** LLM-generated briefing summaries (assembly is deterministic
extraction; a Haiku "summarize the briefing" pass is a future `mode: full` enrichment).
Job/queue semantics — claiming, status, scheduling stay in the orchestrator
(LangGraph/Temporal); document the integration pattern in MA-6 instead. Revisit the
"atomic entity claim" idea from `future-investigations` only if a real consumer needs
work-item locking inside the memory layer.

**Shipped as MA-3a** (core: `retrieve/prime.py` + `POST /v1/prime` + tests + docs). Two
scope adjustments made against what's actually built, both agreed: (1) **entities** are
derived from the retrieved memories' `mentions` (task-relevant + read-scoped) rather than
a tenant-wide `list_entities`; (2) **prior tool runs** rank by `use_count` across the read
agents — *task-semantic step matching* (BM25 over tool args) is a **future enhancement**,
since `steps` aren't in a search view. Adapters (MCP `prime` tool, TS `prime()`, LangGraph
prime node) are **MA-3b**, folded into the adapter PR.

---

## MA-4 — Capture assistant outputs in the Vercel middleware

**Problem.** `wrapGenerate`/`wrapStream` store only `lastUserText(params.prompt)`
(`packages/vercel/src/index.ts:197,205`). Agent A's *output* — its analysis, plan,
answer — is what agent B usually needs, and today it never reaches the brain unless it
went through a tool call.

**Design.**
- `wrapGenerate`: after `doGenerate()` resolves, extract assistant text from
  `result.content` (text parts) and store it as a second turn:
  `content = "[assistant] " + text` with the same ctx (or a `role` metadata field if the
  store contract grows one — prefer the prefix; zero core change).
- `wrapStream`: tap the stream (`text-delta` parts) and store the accumulated text
  **onFinish** — must not delay time-to-first-token; store remains fire-and-forget
  (`void`).
- Config: `captureResponses?: boolean` on `ArangoMemoryOptions`, default **true**
  (symmetric with `captureToolTraces`). Truncate at a sane cap (e.g. 4 kB) before
  storing — long generations dilute retrieval; the episode stays the audit anchor.
- Mirror in the LangChain `remember` node and note in the CrewAI guide (CrewAI's own
  hooks already save task output — verify, don't duplicate).

**Files.** `packages/vercel/src/index.ts` (+ its vitest suite),
`core/src/arango_memory/langchain/` (nodes), adapter docs.

**Tests (vitest).** generate path stores user + assistant texts (two `/v1/store` calls,
assert bodies); stream path stores accumulated text only after stream end; flag off →
single store; failure of the assistant store never rejects the turn.

**Acceptance.** After one wrapped turn, retrieving for content that appeared *only in
the model's answer* returns a hit.

**Out of scope.** Storing full multi-step reasoning traces (tool traces already cover
the act/observe loop); summarizing responses before store.

**Shipped in the adapter surface PR** together with the client halves of MA-1/2/3:
Vercel middleware `captureResponses` (MA-4, default on), `syncWrites` (MA-1b),
`readAgentIds` (MA-2b), and standalone `prime()`/`flush()` helpers (MA-3b/MA-1b); MCP
`prime` + `flush` tools and `read_agent_ids` on `search`. The dungeon app adopts these
in E-1/E-2, not here.

---

## MA-5 — Handoff eval: A writes → B retrieves, scored

**Problem.** The eval suite (LoCoMo) scores single-agent conversational recall;
concurrency tests prove *isolation*. Nothing measures *sharing* — the headline
multi-agent claim is unproven and unguarded against regression.

**Design.**
- New `core/src/arango_memory/eval/handoff.py` + dataset
  `core/tests/data/handoff_smoke.json`: scripted **work sessions** (not chats) — agent A
  ingests facts + records tool steps under a shared namespace; agent B (different
  `agent_id`) issues task-shaped queries via `prime`/`retrieve` with `read_agent_ids`.
  Score: **context recall** (fraction of gold facts present in B's briefing),
  **procedural recall** (gold tool runs surfaced), tokens injected.
- Scenarios (≥4): clean handoff (A→B, shared tier); three-stage pipeline (A→B→C, C needs
  facts from both); distractor noise (irrelevant memories from other agents in-tenant
  must not crowd out gold facts at the token budget); sync-boundary (B reads immediately
  after A's final `sync` store — exercises MA-1, zero sleeps).
- Runner mirrors `eval/benchmark.py` conventions: CLI module, per-scenario progress to
  stderr, targets table, **nonzero exit below targets**; `make handoff-eval`; wire the
  smoke slice into CI next to the existing eval smoke (keyless: FakeEmbedder).

**Files.** `core/src/arango_memory/eval/handoff.py`, `core/tests/data/handoff_smoke.json`,
`core/tests/test_handoff_eval.py`, `core/Makefile`, `.github/workflows` (existing core
job picks it up via pytest), DESIGN §22 (add the harness), §23 (targets: start with
context recall ≥ 0.8 / procedural recall ≥ 0.6 on the smoke slice; tighten with data).

**Tests.** The eval *is* the test; plus unit tests for the scorer (gold-fact matching is
token-overlap like `eval/locomo.py` — reuse, don't reimplement).

**Acceptance.** `make handoff-eval` passes keyless in CI; deliberately breaking
cross-agent reads (e.g. reverting MA-2's AQL) fails the gate.

**Shipped** (`eval/handoff.py`, `tests/data/handoff_smoke.json`,
`tests/test_handoff_eval.py`, `make handoff-eval`). Targets: context recall ≥ 0.8,
procedural recall ≥ 0.6. Reader reads via `prime` (3 scenarios) + one `retrieve`-only
(sync-boundary); the smoke slice gates via pytest in the Core CI job. The planned
separate `sim/` tree was folded into `eval/`.

## MA-6 — Docs: multi-agent orchestration guide

**Problem.** After MA-1..4 the capability exists but is spread across four adapters, two
new endpoints, and a namespacing convention. Nobody can discover the intended pattern.

**Scope.** New `docs/orchestration.md` (linked from README + docs index):
- The pattern: tenant = workspace, agent_id = worker identity, shared namespaces
  (`<crew>::query` / `<crew>::insight`), `read_agent_ids` for reads, `sync`/`flush` at
  stage boundaries, `prime` at stage start.
- A worked pipeline example end-to-end (curl or Python): planner → researcher → writer,
  each stage priming from the previous stage's writes.
- Per-harness recipes: Vercel middleware options; MCP tool sequence for a
  Claude-Desktop-style agent; LangGraph two-node graph with `prime`; CrewAI
  `crew_memory`.
- Integration guidance: what stays in the orchestrator (job queue, retries, scheduling,
  triggering the next agent) vs what the brain provides (context, provenance, history) —
  including the explicit decision that job semantics are out of scope and why.
- Consistency model section (async default, barrier opt-in) — the sharp edge, stated
  plainly.
- Update DESIGN §14 to reflect first-class status and link here.

**Acceptance.** A newcomer can build the three-stage pipeline from the doc alone against
a local compose stack; lychee link check green.

**Shipped** (`docs/orchestration.md`, linked from the docs index + DESIGN §14 + the
adapters index). LangGraph recipe uses the in-process `prime()` call directly (no
`prime` graph node was added). Also fixed the adapters index (9 → 11 MCP tools).

## MA-7 — Per-agent key binding + insight-tier write protection

**Problem.** An API key maps to tenant + scope only (`ApiKeyEntry`); any keyholder may
assert any `agent_id` — one compromised worker key can impersonate every agent and write
directly to `::insight` (which is supposed to be Dream-State-only, currently enforced
purely by adapter convention).

**Design.**
- `ApiKeyEntry` gains `agent_ids: list[str] | None = None` (None → any, current
  behavior). In `_authorize` (`api/app.py`), when the principal restricts agents:
  body `ctx.agent_id` (writes) must be in the list → else **403** (mirror the existing
  tenant-mismatch style); `read_agent_ids` entries outside the list are **silently
  dropped** (reads degrade, never break — §15 spirit). Supports glob-lite suffix
  (`"research::*"`) for crew keys.
- Insight protection: server-side rule — writes to an agent_id matching `*::insight`
  require the principal (or open mode) to carry a new scope value `consolidate`
  (`scope: Literal["read","write","consolidate"]`, ordered write < consolidate). Dream
  State runs in-process (no HTTP hop) so internal consolidation is unaffected.
- OIDC parity: optional `oidc_agent_claim` mapping to the same restriction.

**Files.** `core/src/arango_memory/config.py`, `core/src/arango_memory/security/auth.py`,
`core/src/arango_memory/api/app.py` (`_authorize`), tests
(`test_authz*.py`), `docs/ops.md` + `api.md` key examples, DESIGN §17.

**Tests.** Bound key writing as an allowed agent → 200; as another agent → 403; read
list silently filtered; unbound key unchanged (regression); `::insight` write with
`write` scope → 403, with `consolidate` → 200; open (keyless) mode fully unchanged.

**Acceptance.** A worker key can be scoped to its own agent + the crew's shared tier;
the demo deployment gets a scoped key without any dungeon change (its key lists
`dungeon-player`… i.e. `agent_ids:["dm"]` + tenant as today).

**Shipped.** `ApiKeyEntry.agent_ids` + ordered `scope` (`read < write < consolidate`,
`scope_allows`); `Principal.agent_ids` + `agent_allowed` (glob-lite suffix); `_authorize`
enforces agent binding on writes (403), filters cross-agent reads silently (§15), and
gates `*::insight` writes behind `consolidate`; OIDC parity via `oidc_agent_claim` +
`consolidate` in the scope claim. Wired on `store`/`step` (writes) and `retrieve`/`prime`
(read filter). Fully backward compatible — `agent_ids=None` and open mode unchanged.
Tests: `test_authz_agents.py` + JWT-parity cases (all in-process, CI-gated). Docs:
`ops.md`/`api.md` key examples + `OIDC_AGENT_CLAIM`.

---

## MA-8 — Vector-index reliability + resume P1 benchmark

**Problem.** The brain's retrieval quality is its IQ, and the vector arm is currently
the weak leg: the P1 LoCoMo run is paused on a still-undiagnosed `retrieve degraded`
(see memory: `benchmark-run-state`), and the live demo's vector arm is degraded because
ArangoGraph can't pass `--vector-index`. Real-embedding quality is demonstrated nowhere.

**Scope (bundles the three improvement PRs already identified while troubleshooting).**
1. **Compose hardening:** raise `vm.max_map_count` automatically (privileged init
   service in `docker-compose.yml`) — removes the arangod crash-under-index-build class.
2. **Legible errors:** call `configure_logging()` in `eval.benchmark` / `eval.halu` /
   `ops` CLIs so failures print the actual `AQLQueryExecuteError` reason instead of bare
   `retrieve degraded` (this opacity cost a full day).
3. **n_lists sanity:** lower default `VECTOR_N_LISTS` (e.g. 64) and/or defer index
   creation until corpus ≥ `n_lists × factor`; document the `n_lists ≪ corpus` rule +
   a troubleshooting entry; surface index state in `/health`.
4. **Resume the benchmark** per the saved checklist (clean single arango, mmap fix,
   `down -v`, n_lists=16, capture the real AQL error if it persists) → complete the
   §23 lite run, record results in DESIGN.
5. **Managed-deploy note:** document the ArangoGraph limitation and the supported
   posture (BM25+graph lite mode) in `ops.md` / `orchestration.md`.

**Files.** `docker-compose.yml`, `core/src/arango_memory/eval/*.py`,
`core/src/arango_memory/ops.py`, `config.py`, `schema/` (index gating), `docs/ops.md`,
DESIGN §7/§23.

**Acceptance.** Fresh-clone `docker compose up` + `make benchmark DATASET=converted.json
MODE=lite` completes without manual sysctl or password surgery; any failure prints a
real error string; §23 report recorded.

**Shipped (infra + reliability; the run itself is user-executed).** (1) compose
`sysctl-init` raises `vm.max_map_count` before arangod; (2) the eval/ops CLIs call
`configure_logging()` and the degrade path now logs `str(exc)` (the real AQL reason), not
just the class name; (3) `vector_n_lists` 256→**64** + new `vector_train_factor` (**40**)
defers index creation to `n_lists × factor` docs via `vector_training_threshold`, so IVF
centroids train on enough points; (4) `/health` reports `vector: trained|deferred|unknown`;
(5) `ops vector-diag` surfaces the raw failure; docs cover the `n_lists ≪ corpus` rule, the
rebuild-needed-on-`n_lists`-change gotcha, the resume checklist, and the ArangoGraph
BM25+graph posture. **Deferred (needs the user's machine):** the actual LoCoMo §23 run +
recording results — everything above de-risks it. All CI-gated (338 core tests pass,
including new threshold/health/diag coverage).

---

## RQ-1 — Multi-hop retrieval (query decomposition) — SHIPPED, benchmark-dependent

> **Outcome (2026-07-19):** Built and merged as opt-in `mode="multihop"` (#134–#137). The
> result **depends on whether the benchmark's evidence is actually multi-turn:**
> - **LoCoMo — negative:** lite 0.317 vs multihop 0.132. LoCoMo multi-hop `gold_fact`s are
>   **single evidence turns**, so the questions are multi-hop in *reasoning*, not *retrieval*;
>   the full query matches the one gold turn best and decomposition only dilutes it.
> - **MuSiQue — positive** (200-Q smoke via BX-1, genuinely multi-evidence): **all-hops
>   Recall@k 0.430 → 0.595 (+0.165, +38%)**, recall-frac 0.682 → 0.777, F1 0.307 → 0.376.
>   The strictest metric (whole-chain retrieval) moves most — the mechanism works as designed.
>
> **Verdict:** a correct, opt-in mode that **helps when recall needs ≥2 evidence turns** and
> is neutral-to-harmful on single-turn-gold sets — use it accordingly. The original-query
> superset fix (#137) makes it safe (multihop = single-shot's hits + the extra hops). Full
> analysis in DESIGN §23. The original hypothesis below is retained for the record.

*Scoped, built, shipped. The design below is the original plan; see the outcome note above.*

**Problem.** The P1 LoCoMo run plateaued at **Recall@k ≈ 0.42** (from 0.215 — see
DESIGN §23). Single-shot retrieval maxed out: BM25 carries it, and every ranking bug
(#125–#131) is fixed. The residual gap to the 0.6 target is **category-bound**:

| Category | Recall | Ceiling reason |
|---|---|---|
| single-hop | ~0.36 | reachable — BM25 finds the one evidence turn |
| temporal | ~0.33 | reachable |
| **multi-hop** | **~0.19** | **the anchor** — see below |

A multi-hop question — *"Where does the person Alice met at the reunion work?"* — needs
**two evidence turns that don't co-locate near one query embedding**: turn A ("Alice met
Bob at the reunion") and turn B ("Bob works at Acme"). The query matches A but not B; B is
only findable *after* "Bob" is known. No single top-k pass over any arm weighting can
gather that chain. The existing **graph arm** expands one hop, but it seeds from the
*query's* hits and expands by static `relates_to` edges — it surfaces "things related to
Alice," not "Bob's employer." RQ-1 makes the second hop **query-directed** via the
generator. This is the one lever left, and it's a retrieval **mode**, not a knob.

**Approach (locked): decomposition first.** One generator call splits the question into the
minimal set of independent lookups; retrieve each with the existing fused `retrieve()`;
union + re-fuse; answer from the union. Deterministic, bounded latency, no agentic loop.
The iterative **read→retrieve→read** variant (handles dependent chains where hop 2 needs
hop 1's *result*) is strictly more powerful but is a serial loop — **deferred**; add it only
if multi-hop recall stalls below ~0.35 after decomposition. LoCoMo multi-hop is dominantly
*parallel* evidence (both facts stated independently), so decomposition should capture most
of the lift; measure it before paying for the loop's complexity.

**Algorithm.**
```
retrieve(mode="multihop", Q):
  subqs = decompose(Q, generator)              # → [Q] if model returns 0/1 lookups
  if len(subqs) <= 1: return _retrieve_impl(Q) # transparent single-shot fallback
  lists = [ _retrieve_impl(sq, k=sub_k) for sq in subqs[:decompose_max_subqueries] ]
  fused = rrf_fuse(lists)                       # each sub-query is an arm at weight 1.0
  selected = mmr(fused, k)                      # unchanged
  context = assemble_tiered(selected, budget)  # same token budget
  answer from context                          # locomo #131 step already does this
```
- **Transparent fallback** (0/1 sub-questions ⇒ identical to today's `retrieve()`) is the
  safety property: a mis-fire never scores *below* single-shot, it just adds nothing.
- **Fuse via `_rrf_fuse`**, each sub-query's list as an input arm at weight 1.0 (all are
  true relevance rankers, unlike the bm25/vector/graph asymmetry). A doc found by *multiple*
  sub-questions accumulates RRF mass — exactly the multi-hop signal to reward. Reuses
  existing fusion math; no new code.
- **Budget unchanged:** more sub-queries widen the candidate *pool*, not the injected
  context — downstream token cost to the agent stays flat.

**Surface (locked): new `mode="multihop"`.** Extend the `mode` Literal to
`lite|full|multihop`, orthogonal to full-mode enrichment (HyDE/gate). Default stays `lite`;
`multihop` is opt-in like HyDE, with documented N× latency (outside the §23 lite target).

**Files.**
- `retrieve/decompose.py` (new) — `decompose(query, *, generator, cache) -> list[str]` +
  `_DECOMPOSE_SYSTEM`, mirroring the `enrich.py` pattern (system prompt + one
  `generator.complete()` + `QueryCache` memoization).
- `retrieve/search.py` — `multihop` branch in `_retrieve_impl`; factor the per-query core so
  it's callable per sub-query.
- `config.py` — `decompose_max_subqueries: int = 4` (ge=1, le=8);
  `decompose_max_hops: int = 0` (reserved for the iterative variant; 0 = off).
- `api/app.py` + models — extend the `mode` Literal.
- `eval/locomo.py` — already threads `mode`; run with `mode="multihop"`.
- `docs/DESIGN.md §9`, `docs/ops.md` — document the mode, knobs, latency.
- tests — `test_decompose.py` (prompt/parse/fallback via `FakeGenerator`), a multihop-path
  test in eval/search.

**Acceptance.** Measured on the **real** LoCoMo dataset (not the smoke slice):
multi-hop recall rises materially above 0.19 (target this pass: **≥ 0.30**); single-hop and
temporal **do not regress** (guaranteed by the transparent fallback); overall Recall@k moves
toward 0.6; latency documented; default-mode latency unchanged.

**Risks.** (1) Bad decomposition retrieves noise — bounded by the sub-query cap + fallback;
worst case ≈ single-shot. (2) Latency multiplier — bounded by the cap, off the hot path.
(3) Eval cost — a real-generator multihop run over 1531 Qs makes N× retrievals + a decompose
call each; smoke on the multi-hop subset first and estimate cost before the full run.

---

## BX-1 — Benchmark expansion: MuSiQue multi-evidence dataset + metric

*Scoped, not started. Prerequisite for a meaningful RQ-2 and a fair RQ-1 re-trial.*

**Why.** RQ-1 could not be tested on LoCoMo because its multi-hop `gold_fact` is a **single
evidence turn** — recall (`_recall_hit`: one substring in any hit) rewards finding that one
turn, so decomposition has no headroom and its dilution only hurts (DESIGN §23). To measure
*multi-hop retrieval* — or any retrieval-quality lever — honestly, the benchmark must pair
**multi-evidence questions** with a **multi-evidence recall metric**. A better dataset alone
does nothing without the metric change; the metric is the load-bearing part.

**Dataset (chosen): [MuSiQue](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00475/110996) (MuSiQue-Ans).**
24.8k 2–4-hop questions, **non-shortcuttable** by construction (Disconnection Filtering forces
each hop to depend on the prior — a single-paragraph baseline drops from ~65 F1 on HotpotQA to
~32 here), with **sub-question decomposition + paragraph-level support annotations**. It is the
only option that ships decomposition labels, so it directly re-trials the RQ-1 hypothesis on
data that is provably multi-hop. Alternative if a RAG-native, news-domain set is preferred:
[MultiHop-RAG](https://arxiv.org/abs/2401.15391) (2,556 queries, evidence across 2–4 docs).
Domain mismatch (documents vs conversation turns) is acceptable — our ingest is generic (a
supporting paragraph is just a memory) and we are testing retrieval *mechanics*.

**Design.**
- **Converter** (`eval/musique_convert.py`) → our dataset schema, but `QA.gold_fact: str`
  becomes `gold_facts: list[str]` (the support set). Ingest each supporting paragraph as a
  memory; keep the single-`gold_fact` path working (wrap as a 1-element list) so LoCoMo runs
  are unaffected.
- **Metric** (`eval/locomo.py`) — `_recall_hit` gains a multi-evidence mode: **fraction of the
  support set retrieved** (report both mean fraction and all-hops-present@k). This is what makes
  the benchmark able to reward gathering a chain.
- **Runner** — dataset-agnostic; a `--dataset-kind musique` flag or auto-detect on the schema.

**Acceptance.** MuSiQue converts + runs end-to-end; the multi-evidence recall metric is unit-
tested; LoCoMo numbers are unchanged (backward-compatible single-fact path). No retrieval-code
change — this is eval infra.

**Decisions to make at scope time.** all-hops-present vs fractional recall as the headline
(recommend reporting both, fractional as primary); MuSiQue-Ans subset size for a smoke;
paragraph-as-memory granularity (whole paragraph vs sentence).

---

## RQ-2 — Close the retrieval-content gap (diagnostic → reranker / expansion)

*Scoped, not started. Depends on BX-1 for a dataset with headroom; diagnostic-first.*

**Problem.** After the ranking fixes, the residual gap from Recall ≈ 0.42 → the 0.6 target is
a **retrieval-content** gap: question↔evidence lexical overlap ≈ 0.27 (DESIGN §23), so BM25
misses evidence that shares little vocabulary with the question, and the vector arm ranks by
query-proximity rather than relevance. But "content gap" has **two opposite failure modes**,
and building the wrong fix wastes effort:
- **Ranking failure** — the gold *is* in the candidate pool but ranked outside top-k → a
  **reranker** fixes it.
- **First-stage recall failure** — the gold is *absent from the pool* → a reranker cannot
  help; you need better first-stage recall (query expansion, stronger embeddings, richer
  `prospective_queries`).

**Design — diagnostic-first** (the measure-before-building discipline that saved three tuning
runs on RQ-1):
- **RQ-2a — diagnostic (cheap, no new model). Scoped in detail; decisions locked.** For every
  support item that *misses* (not in top-k), classify it against the **full fused candidate
  pool** (BM25 ∪ vector ∪ graph, post-RRF, pre-MMR/truncation), gathered in **lite single-shot**
  at the default **`candidate_pool=100`**:

  ```
  in top-k hits?            → HIT           (not a miss)
  else in the fused pool?   → RANKING miss  (a reranker can recover it)
  else (absent from pool)   → RECALL  miss  (first-stage retrieval must be fixed)
  ```

  The aggregate split (overall + per-category) is the whole deliverable — it picks RQ-2b's
  lever. **Files:** `retrieve/search.py` — add `diagnose_pool(...)` returning the full ranked
  fused pool via the existing `_gather_fused` (read-only, off the hot path, mirrors
  `diagnose_vector`); `eval/pool_diag.py` (new) — per-question classify `support()` items via
  the existing normalized-substring match, aggregate, CLI
  `python -m arango_memory.eval.pool_diag DATASET.json [--k 10] [--pool 100]`; `test_pool_diag.py`;
  ops.md run steps. No change to `retrieve()`'s hot path. **Acceptance:** produces the
  ranking-vs-recall split reproducibly on the MuSiQue 200-Q set (real embeddings).

  > **RQ-2a result (2026-07-20, MuSiQue 200-Q, #144):** of 400 support items, **303 hit /
  > 97 miss**, and the misses split **100% ranking (in-pool) / 0% recall (absent)**. Every
  > missed gold paragraph is already in the fused pool — the first stage finds all the
  > evidence; the failure is **purely ranking**. → **RQ-2b = cross-encoder reranker** (query
  > expansion has no gap to close). Headroom: 97/400 items (24%) recoverable by reordering.
  > *Caveat:* each MuSiQue question is its own ~20-paragraph tenant, so `pool=100` ⊇ the whole
  > corpus and a recall-miss is structurally near-impossible here — this proves the failure is
  > ranking **in the given-context setting** and validates the reranker, but does not test
  > first-stage recall on a large open corpus (that would need a pooled-corpus variant).

- **RQ-2b — cross-encoder reranker (lever, decided by 2a). Scoped; decisions locked.** Insert
  a reranker between fusion and MMR: fused pool (candidate_pool) → cross-encoder scores each
  `(query, text)` jointly → reorder → existing MMR + tiered assembly on the reranked order. A
  cross-encoder scores relevance directly (not lexical/proximity), so it recovers the 97
  in-pool-but-unranked golds. One hook in `_retrieve_impl` after `_gather_fused`; MMR/assembly
  unchanged (they consume `fused_score`).

  **Locked decisions:**
  - **Provider — local cross-encoder** (sentence-transformers, e.g. `bge-reranker-base` /
    ms-marco MiniLM). Pluggable `Reranker` protocol + a **`FakeReranker`** (deterministic,
    keyless) mirroring `Embedder`/`Generator`, so CI stays offline.
  - **Surface — composable `rerank=true` flag** (config/opts, orthogonal to `mode`), so it
    stacks on lite **or** multihop; not its own mode.
  - **Scoring — replace:** `fused_score := rerank score` for the reranked top-N; MMR then
    orders by pure cross-encoder relevance (cleanest measure of the lever's lift).
  - **`rerank_top_n`** bounds cost (default e.g. 50). Off the lite hot path; **degrades** to
    the fused order if the model is unavailable/errors (§15) — never breaks the turn.

  **Files:** `retrieve/rerank.py` (protocol + Fake + local provider), a `_rerank` hook in
  `search._retrieve_impl`, `config.py` (`rerank_enabled`, `rerank_top_n`, `reranker_provider`,
  model id), `eval` wiring (a `--rerank` benchmark flag), docs, tests.

**Acceptance.** Measured on **MuSiQue** (BX-1): the reranker lifts `recall-frac` / all-hops
`Recall@k` materially over the fused baseline (0.682 / 0.430) toward the diagnostic ceiling
(per-item recall 0.76 → ~1.0), without regressing lite-mode latency; degradation path tested.

---

## BX-2 — pooled-corpus MuSiQue (open-retrieval variant)

*Scoped, not started. Decisions locked. Eval infra only — no retrieval-code change.*

**Why.** Every MuSiQue result so far (lite/rerank/multihop/stacked) is in the **given-context**
regime: the converter makes each question its own **~20-paragraph tenant**, so `pool@100` ⊇
the whole corpus and the gold is *always* in the pool. That's why RQ-2a measured **0%
first-stage-recall misses** — an *artifact*, not a finding. We have never tested whether
BM25+vector+graph can **find** the gold among real distractors. BX-2 pools all questions'
paragraphs into **one shared corpus (one tenant)** so retrieval is genuinely open (thousands
of candidates), answering two open questions: (1) is first-stage recall a real gap on open
corpora (do out-of-pool misses finally appear in `pool_diag`)? (2) do the rerank/multihop/
stacked gains survive the harder regime?

**Design (locked).**
- `musique_convert.py` gains a **`--pooled`** flag (`convert(..., pooled=True)`): emit **one
  Sample** whose `sessions` hold every paragraph across the selected questions, and whose `qa`
  is **all** those questions (each keeping its `gold_facts`). Single `sample_id` = one tenant
  = one open corpus.
- **`--limit N` selects the questions**, and only *those* questions' paragraphs are pooled;
  the **same N questions are scored** — self-contained, so every gold is guaranteed present
  in the corpus and a miss is a *true* first-stage-recall failure (not a missing document).
- **Dedup** paragraphs by `(title, paragraph_text)` — MuSiQue reuses Wikipedia paras across
  questions; without dedup the corpus balloons and double-counts.
- Output is the standard schema, so `benchmark`, `pool_diag`, `--rerank`, `MODE=multihop` all
  run on the pooled file unchanged.

**Files.** `eval/musique_convert.py` (the `--pooled` branch + dedup), `test_musique_convert.py`
(pooled mapping: shared corpus, dedup, all questions retained), ops.md (build + run steps;
"open-retrieval stress test").

**Acceptance.** A pooled `musique-pooled.json` converts (one sample, N questions, deduped
corpus); `pool_diag` on it yields a **non-trivial out-of-pool (recall) miss fraction** — i.e.
it can surface the first-stage-recall failures the per-question setup structurally couldn't.
Then lite/rerank/multihop can be re-measured in the open-corpus regime.

**Decisions to make at build time.** dedup key exactness (title+text vs normalized); whether
to cap corpus size independently of `--limit` if ingestion is too slow; whether `pool_diag`'s
`--pool` should widen for the larger corpus.

> **BX-2 run outcome (scalability wall).** The first real pooled run (3,075 paragraphs, one
> tenant) took **~12 h to ingest** and then **timed out on retrieval** (`ReadTimeout` at 60 s),
> so it produced no usable recall split. Two bottlenecks the per-question 20-doc tenants had
> masked: (1) ingestion is **~O(n²)** — every `store()` resolves extracted entities against all
> existing entities in the tenant, which grows with corpus size; (2) the **graph arm fans out**
> on a dense single-tenant `relates_to` graph, blowing the retrieval timeout. → motivates BX-3
> (a lightweight probe that routes around both) and a separate scalability investigation
> (unscheduled).

---

## BX-3 — lightweight pooled diagnostic (routes around the BX-2 scalability wall)

*Scoped, not started. Decisions locked. First-stage-recall probe only — no scalability fix.*

**Why.** BX-2's pooled run hit a wall (O(n²) ingestion + graph-arm retrieval timeout, above).
But first-stage recall — "do BM25 + vector surface the gold among distractors" — needs
*neither* entity extraction nor the graph arm. BX-3 measures exactly that by skipping both, so
the open-corpus recall question is answerable in **minutes, not hours**.

**Design (locked).**
- **`store(..., extract=True)` param** (core): gate the existing `write_entities` block
  (`if is_new and not is_working and extract`). Default `True` = unchanged; `False` skips
  entity/edge extraction → no O(n²) resolution, no graph built. Minimal, generally reusable.
- **`pool_diag --lightweight` flag**: ingest with `extract=False` and probe with
  `graph_hops=0` (both the top-k `retrieve` and `diagnose_pool`). Measures first-stage recall
  (BM25 + vector) cleanly.
- **Record the scalability finding** in DESIGN §23 (the BX-2 outcome above); the O(n²)
  ingestion / graph fan-out **fix is a separate, unscheduled investigation** — BX-3
  deliberately routes around it, does not fix it.

**Files.** `ingest/store.py` (`extract` param), `eval/pool_diag.py` (`--lightweight` →
extract-skip ingest + `graph_hops=0`), tests (extract=False writes no entities; lightweight
path), ops.md, DESIGN §23.

**Acceptance.** A `--lightweight` pooled run over ~200 questions completes in minutes with no
retrieve timeouts and yields a real ranking-vs-recall split for first-stage recall on the open
corpus — the number BX-2 set out to get.

---

## SC-1 — single-large-tenant scalability (ANN entity resolution + bounded graph fan-out)

*Scoped, diagnostic-first. The highest-leverage open item: a long-lived agent's normal
end-state is a large single tenant, and BX-2/BX-3 proved the system stalls there
(~12 h to ingest 3k memories; retrieval times out).*

**Mechanism — confirmed by code, not just hypothesis.**
- **Ingestion is O(N²).** `entities._FETCH_EXISTING` (in `ingest/entities.py`) fetches **every
  entity in the tenant, *with its embedding*,** on every `store()`, then matches in Python by
  cosine. There is **no vector index on `entities`**. So per-write cost is O(N) transfer +
  O(N) compares and grows as the tenant fills → O(N²) overall.
- **Retrieval fans out.** The graph arm's `relates_to` traversal over a dense single-tenant
  entity graph blows the 60 s query timeout (the fan-out tamed at 200 docs, unbounded at 3k).

**Design — diagnostic-first, then two fixes.**
- **SC-1a — profiling harness (diagnostic, no core change).** Ingest into one tenant at growing
  sizes (e.g. 500 → 3,000) and record per-`store()` and per-`retrieve()` p50/p99 vs size, to
  (1) confirm the O(N²) ingestion curve + the retrieval blow-up and (2) set the baseline the
  fixes are verified against.
- **SC-1b — ANN entity resolution (the ingestion fix).** Add a **vector index to `entities`**
  (reuse the MA-8 IVF / `APPROX_NEAR_COSINE` machinery the `memories` arm already has) and
  replace the `_FETCH_EXISTING` full-scan with a **top-k nearest-entity** query per extracted
  entity — per-write O(N) → ~O(k). **Behaviour-preserving:** same `entity_merge_threshold`,
  only faster candidate generation; fall back to the scan while the index is cold/untrained
  (as the memory arm does, §7). Re-run SC-1a to prove the curve flattened.
- **SC-1c — bounded graph fan-out (retrieval fix, conditional on SC-1a).** ✅ Cap the traversal
  (neighbours per entity / seed count / degree) so retrieval stays bounded on a dense tenant.
  The graph arm is already down-weighted (`RRF_GRAPH_WEIGHT=0.1`), so a cap costs little
  quality. Shipped as `GRAPH_MAX_NEIGHBORS` (200) — a `LIMIT` on the `relates_to` breadth.
- **SC-1d — bounded per-entity memory fan-out (SC-1a follow-up).** ✅ The SC-1a profiler showed
  SC-1b flattened ingestion (store p50 plateaus ~1.4 s) but **retrieve still grew** (798 →
  5,080 ms over 500 → 3,000): SC-1c capped the *neighbour* breadth but not each entity's
  `INBOUND mentions`, which grows as hub entities accrue mentions. `GRAPH_MAX_MEMORIES_PER_ENTITY`
  (50) caps that join ⇒ total graph work `MAX_NEIGHBORS × MAX_MEMORIES_PER_ENTITY`,
  tenant-size-independent. Default leaves real corpora untouched; only pathological hubs cap.

**Files.** `schema/collections.py` (entities vector index), `ingest/entities.py` (ANN
resolution + cold-start fallback), `retrieve/search.py` (`_GRAPH_QUERY` fan-out caps),
`config.py` (entity-index + fan-out knobs), a profiling script under `eval/` or `ops`, tests,
docs.

**Acceptance.** On the profiling harness, per-`store()` latency is ~flat (not linear) in tenant
size after SC-1b, and a pooled-corpus ingest + retrieve that previously stalled completes in a
reasonable time with no timeouts. Retrieval quality on the given-context benchmarks is
unchanged (behaviour-preserving resolution + down-weighted graph).

**Decisions locked (recommended).** candidate generation = **ANN vector index on `entities`**
(reuses MA-8; not blocking/LSH); **cold-start fallback to scan** while the entity index is
untrained; graph fix = **cap fan-out** (don't remove the arm). Open at build time: entity
`n_lists`/`train_factor`; the top-k and fan-out caps (tune on the harness).

---

## RT-1 — expose `candidate_pool` as a config + API knob (open-corpus tuning)

*Scoped. The cheap, direct payoff of the BX-3 finding.*

**Why.** BX-3 showed ~15% of open-corpus recall misses are *tail-reachable* by widening the
candidate pool past 100 (then rerank promotes them). But `candidate_pool` is a hardcoded
`retrieve()` arg default — no `CANDIDATE_POOL` env var, not in the API — so a large-tenant
deployment can't act on the finding (`rerank_enabled` already is tunable; the pool isn't).

**Design.** `config.py`: `candidate_pool: int = Field(default=100, ge=1)`. `retrieve()`:
`candidate_pool: int | None = None` → `settings.candidate_pool` (the `None → settings` pattern
`graph_hops`/`n_lists` use). API `RetrieveOptions`: add `candidate_pool`, threaded to
`retrieve`. Docs: an "open-corpus / large-tenant tuning" note (raise `CANDIDATE_POOL` + enable
rerank; trade is per-query latency). Tests: default, settings-default honoured, API thread.
**Keep the global default at 100** — widening it for everyone taxes the common given-context
path; the finding is open-corpus-specific, so make it *tunable*, not *bigger-by-default*.

**Acceptance.** `CANDIDATE_POOL` settable via env + `/v1/retrieve` opts; default runs are
byte-identical to today; docs explain the tuning. Size S.

---

## RQ-3 — Rerank scoring: replace vs blend (does the reranker throw away time?)

*Done — **negative result, `replace` stays the default** (DESIGN §23, rev 93).* `event_time` reliably
put updates above stale facts (held-out KU newest-above-stale 0.568 → 0.773, p=0.004) but KU answers
didn't improve and temporal-reasoning collapsed (0.654 → 0.368, p<0.001): IN-4's dates in the
assembled context already let the answerer resolve updates, and a global newness prior evicts older
evidence. `rrf` was neutral everywhere. Any future newness signal must be scoped to conflicting
statements of the same fact, not global recency.

*Tooling notes:* Two findings from building it: variants
must be scored with **read-only** retrieval probes (`record_access=False`) — a normal retrieve
refreshes `accessed_at`, which feeds decay and would let one variant perturb the next; and
`parse_explicit_time` can't order LongMemEval session dates (it reads `2023/05/20 (Sat) 02:21` as a
bare year), so `event_time` scoring uses its own date+time sort key.

**Why.** RQ-2b's locked decision *replaces* `fused_score` with the cross-encoder score for the
reranked top-N (clean measurement of the reranker's lift; the two scores aren't on a common scale —
RRF is `Σ w/(60+rank)` ≈ 0.01–0.05, `bge-reranker` a sigmoid relevance probability in [0, 1]). The cost:
inside the reranked block we discard (a) **recency** and (b) **arm consensus** (a hit found by
BM25 *and* vector *and* graph). For a memory system that's suspicious — the knowledge-update case is
exactly "the same fact, stated twice; the newer one should win", and a cross-encoder scores the
stale and fresh statements as equally relevant.

**The load-bearing design finding (read before building).** The fused score's recency is
**access recency**: `effective_strength(strength, accessed_at, now)` (`lifecycle/decay.py`) — wall-
clock ingest/access time, *not* content time. LongMemEval ingests each question's whole history in
one `store_many` batch with a single `now`, so **every candidate carries an identical decay factor**.
A blend that "restores the fused score's recency" is therefore a **null by construction** on
LongMemEval. To move knowledge-update the variant must use **content time** — `event_time`, the
session date each memory already carries as a field (IN-4). (Access recency still matters in
production, where it varies — but no offline benchmark here can measure it.)

**Data supports a sharp test.** In LongMemEval-S, **all 78 knowledge-update questions have ≥2
evidence sessions** (the stale fact + the update), turns carry `has_answer` labels (10,960 labelled
turns), and sessions carry `haystack_dates`. So "is the *newest* evidence ranked above the *stale*
evidence?" is measurable **deterministically**, with no LLM judge in the loop. Type sizes in the full
set: knowledge-update 78, temporal-reasoning 133, multi-session 133, single-session-user 70,
-assistant 56, -preference 30 (500 total). The stratified-90 (n=15/type, ±1–2 questions of judge
noise = ±0.07–0.13) is **too small** to detect this effect — use the full per-type slices.

**Variants** (new `rerank_scoring: Literal["replace", "rrf", "event_time"] = "replace"` in
`config.py`, applied inside `search._rerank`; default keeps today's behaviour byte-identical):
- **`replace`** — current RQ-2b behaviour (the baseline).
- **`rrf`** — rank-level blend: `1/(60 + rank_ce) + 1/(60 + rank_fused)` over the head. Rank-based,
  so no score calibration; carries arm consensus (and, in production, access recency) back in.
- **`event_time`** — cross-encoder relevance plus a content-time prior: take the CE relevance
  probability (already sigmoid-scaled by sentence-transformers — the runs wrongly applied a second
  sigmoid; see the DESIGN §23 correction), then add `β · newness(event_time)` where newness ranks the head's
  `event_time`s (newest = 1). Candidates without `event_time` get a neutral prior.
- Weights/`β` are **fixed a priori or tuned on a dev split** (e.g. 26 of the 78 KU questions),
  reporting on the held-out remainder only — never tuned on the reported set.

**Metrics.**
1. **Update ordering (primary, deterministic):** over the 78 KU questions — is a `has_answer` turn
   from the *newest* evidence session in top-k, and ranked above any stale-evidence turn? Plus
   evidence recall@k (fraction of answer sessions with ≥1 `has_answer` turn retrieved).
2. **End-to-end judged accuracy** on knowledge-update (78) + temporal-reasoning (133) + overall,
   **paired per question** across variants; significance via McNemar's test on the discordant pairs.
3. **Guardrails (must not regress):** LoCoMo lite+rerank Recall@k (0.700, n=1,531) and MuSiQue
   all-hops rerank (0.565, n=200). Neither has a useful `event_time` signal, so a blend that dilutes
   the cross-encoder there is a real risk.

**Build plan.**
1. `longmemeval_convert`: opt-in `--evidence` flag that carries per-turn `has_answer` + session date
   into the converted QA (today `gold_fact` is intentionally left empty).
2. `_rerank`: the three scoring modes behind `rerank_scoring` (unit-tested like the RQ-2b tail tests —
   pure functions over `_Candidate`, incl. negative logits and missing `event_time`).
3. Harness: `--rerank-scoring replace,rrf,event_time` **evaluates every variant against one ingest**
   and emits a per-variant report + the paired table. Must not be done by re-running the harness on
   the same `ARANGO_DB`: `store_many(extract=True)` has no replay guard, so a second ingest would
   double-count graph beliefs (filed separately).
4. Runs: KU + TR slices (211 Qs), graph-on spaCy (keyless) + `RERANKER_PROVIDER=local`, `--concurrency`.
   Cost is answer+judge only: ~211 × 3 variants × 2 Haiku calls ≈ 1.3k calls (low single-digit $);
   extraction is keyless. Then the LoCoMo + MuSiQue guardrail runs.

**Decision rule.** Make a blend the default only if it improves KU update-ordering with paired
significance **and** holds KU/TR/overall judged accuracy **and** passes both guardrails. Otherwise
keep `replace` as the default, ship the winning blend as an opt-in knob (or drop it), and record the
null in DESIGN §23 — either outcome is publishable. Size M.

---

## OBS — Obsidian adapter (the vault as a memory surface)

**Why.** Obsidian users already hand-build a personal knowledge graph: notes are nodes,
`[[wikilinks]]` are edges. That is *exactly* the shape this core produces automatically
(memories → entities → typed relations). An Obsidian adapter closes the loop both ways — a
vault becomes durable agentic memory, and the agent's memory becomes browsable in Obsidian's
native graph view. It's also the first adapter aimed at an **end user's** knowledge base rather
than an agent framework (Vercel/MCP/LangChain/CrewAI), widening who the core is for. The core
API is adapter-neutral, so this is additive — no core changes required for the MVP.

Phased, each independently shippable:

### OBS-1 — Vault → memory ingestion (markdown + wikilinks → graph)

**Scope.** A markdown ingestion path that maps a vault onto the core cleanly:
- Each note (or heading-delimited block) → a memory via `/v1/store`; note path/title + frontmatter
  → provenance/metadata; `tenant_id` = vault (per-vault brain).
- `[[wikilinks]]` and `#tags` → **explicit entity relations** (not just co-occurrence) — the link
  graph the user already curated becomes first-class edges, so the graph arm starts strong.
- Frontmatter dates (`created`/`modified`) → `event_time` / `valid_time` (bi-temporal, IN-4).
- Idempotent + incremental: re-ingest only changed notes (hash/mtime), so a vault re-sync is
  cheap and safe (matches the idempotency-key store path).

**Files.** A converter/importer (`core/src/arango_memory/obsidian/` or an `eval`-style CLI), tests
on a fixture vault. **Open question to decide first:** core-side importer vs. plugin-side parsing
(lean plugin-side so the core stays vault-agnostic; the importer is a thin convenience).

### OBS-2 — The Obsidian plugin (`packages/obsidian/`)

**Scope.** A TypeScript community plugin (mirrors `packages/vercel`'s thin-client stance — no
memory logic, just calls `/v1`). Commands + a settings pane (core URL, API key, tenant):
- **Ingest vault / changed notes** → memory (OBS-1 path).
- **Recall**: semantic `/v1/retrieve` over the vault's memory from a command or side panel,
  insert results at the cursor or open a results view — retrieval that spans what plain search
  can't (fusion + graph).
- **Surface the graph** (stretch): render the core's entities/relations back as notes/canvas so
  Obsidian's graph view visualizes memory the agent added beyond the hand-authored links.
- Shares the brain with the MCP server (same core, same `tenant_id`): what an agent writes via
  Claude Desktop shows up in the vault, and vice-versa — the shared-brain story, made tangible.

**Files.** `packages/obsidian/` (plugin manifest + build), a typed core client (reuse the pattern
in `packages/vercel/src`), vitest.

### OBS-3 — Example vault + docs

**Scope.** A small worked example vault under `examples/obsidian-memory/` + a README (ingest →
recall storyboard, à la `examples/mcp-memory`), `docs/adapters/obsidian.md`, and links from the
top-level README adapter list. CI smoke on the OBS-1 ingestion against the fixture vault (keyless).

**Success.** Point the plugin at a vault + a running core → notes ingest, `[[links]]` become graph
edges, and a natural-language recall surfaces the right notes; the agent (via MCP) and the user (via
Obsidian) read/write one brain. Decide OBS-1's core-vs-plugin split before building.

---

## Explicitly out of scope (decided, with reasons)

- **Job/task queue semantics** (claim, status, schedule, retry): the orchestrator's job
  (LangGraph, Temporal, queues). The brain provides context + provenance + history;
  MA-6 documents the seam. Revisit "atomic entity claim" only on concrete demand.
- **Event push / webhooks / changes feed** ("wake agent B when A writes X"): valuable
  but a new infrastructure class (delivery guarantees, subscriber state). Poll via
  `retrieve`/`prime` for now; design doc first if demand materialises.
- **Doc-level ACLs / visibility enums**: namespacing + `read_agent_ids` + MA-7 key
  binding cover the known consumers with far less complexity.

---

## CW-1 — Port the CrewAI shim to crewai ≥ 1.10 unified memory  ✅

**Why.** `to_crewai_storage()` subclasses crewai's legacy `crewai.memory.storage.interface.Storage`
for `Crew(external_memory=ExternalMemory(...))`. CrewAI 1.10.0 removed both in favour of a unified
memory backend (`crewai/memory/storage/backend.py`), so the shim only works on `crewai<1.10`. For
0.1.0 it raises a clear `ImportError` on newer crewai; `ArangoCrewStorage` / `crew_memory()` are
crewai-free and unaffected. Capping the extra at `<1.10` was rejected — it pins an advisory-bearing
json-repair.

**Scope.** Implement the unified-memory backend protocol over `ArangoCrewStorage` (keep the
G-Memory tier namespacing), and replace the stubbed-crewai shim test with one against a real
crewai install so an upstream interface change can't slip past CI again.

**Outcome.** Shipped as `arango_crewai_memory()` (`crewai/unified.py`): a `StorageBackend` plus a
**paired embedder**. crewai's `search()` receives only a query embedding; the paired embedder is the
core's embedder remembering the text behind each vector, so `search()` recovers the text and runs the
core's full hybrid retrieval (exact-cosine fallback for foreign vectors). This was chosen over a plain
vector-store backend, which would have reduced ArangoDB to one more vector store and dropped BM25, the
graph and rerank. crewai's per-record fields live in a `crewai_records` side collection (covered by
tenant `forget`/`purge`); each record revision gets its own core memory key, so deletes and rewrites
never collide with the core's keep-the-soft-deleted-key rule. Scores are cosine (crewai's Qdrant
convention). A `crewai` CI job (`make test-crewai`) tests against real crewai. The legacy shim remains
for `crewai<1.10`.

---

## MCP HTTP follow-ups (from #255)

#255 added the Streamable HTTP transport (`python -m arango_memory.mcp --transport http`) with
pass-through bearer auth: each caller's own core key / JWT is forwarded, so the core's ABAC applies
per caller. These were scoped out of it:

### MCP-1 — MCP OAuth authorization flow

**Why.** Pass-through bearer works for clients that let you set a header (Claude Code, Cursor, custom
agents). Clients that only connect to remote MCP servers through the spec's OAuth flow — e.g. Claude
Desktop / claude.ai custom connectors — can't use it; they expect protected-resource metadata and an
authorization server to obtain tokens from.

**Scope.** Serve OAuth protected-resource metadata from the MCP server (the SDK's `AuthSettings` +
`TokenVerifier`), pointing at an authorization server that issues tokens the core already accepts
(its JWT verification, §17), so tenant/agent/scope claims keep driving ABAC. Decide first: bring an
external IdP (Auth0/Okta/Cognito) vs. a minimal built-in issuer. Pass-through bearer stays as the
simple mode.

### MCP-2 — Packaged MCP HTTP deployment  ✅

**Why.** The container image is built with `uv sync --no-dev` and no extras, so it can't run the MCP
server; HTTP mode today is a `pip install "arango-memory[mcp]"` + run-it-yourself process.

**Scope.** Include the `mcp` extra in the image (or publish a second tag) so the same image can run
`python -m arango_memory.mcp --transport http --host 0.0.0.0 --allowed-hosts …`; document a
reverse-proxy/TLS recipe; optionally add it as a Railway service beside the live core (needs the
core in enforced mode — it is — and a public `--allowed-hosts`).

**Outcome.** The image now syncs the `mcp` extra (+1.5 MB; runtime + `mcp` audits clean), so one
image runs the core (default command) or the MCP server
(`python -m arango_memory.mcp --transport http`). Along the way: the venv goes first on `PATH` (bare
`python` in the container was the base image's), the healthcheck URL is configurable, the MCP HTTP
server has an unauthenticated `/health`, and its port falls back to `PORT`. There is a
`docker compose --profile mcp` service, Caddy TLS + Railway recipes in `docs/adapters/mcp.md`, and a
CI smoke test of the MCP role. **Not done:** actually deploying a hosted MCP service beside the live
core — that's an infrastructure decision, left to the owner.

### MCP-3 — Legacy HTTP+SSE transport (conditional)  ✖ closed — won't do

**Why.** The MCP spec deprecated the older two-endpoint HTTP+SSE transport in favour of Streamable
HTTP, so it was deliberately not added. Some older clients may still only speak it.

**Scope.** Only if a client we need can't use Streamable HTTP: FastMCP also serves `sse_app()`; mount
it behind the same bearer gate and host/origin checks. Otherwise, close as won't-do.

**Decision (2026-10-01): closed as won't-do.** No client we need is limited to the legacy
transport. Mainstream clients (Claude Code, Cursor, the official SDKs) speak Streamable HTTP, which
the spec adopted in place of HTTP+SSE in 2025. Building it anyway would add a deprecated, **stateful**
transport: its `GET /sse` stream and `POST /messages?session_id=…` pair keeps the session in one
process's memory. That needs a single replica or sticky sessions, against the stateless,
load-balanced design of the Streamable HTTP mode, and it's more surface to maintain and secure.
**Reopen if** a concrete client we need can't use Streamable HTTP. The work is then about a day:
an opt-in `--transport sse` from the SDK's `sse_app()`, behind the same bearer/OAuth auth and
host checks, documented as single-replica only.

---

## IMG-1 — Move the container image to a newer Python  ✅

**Why.** After the OS-layer hardening, the only fixable findings left in the image are advisories
against the CPython 3.11.16 interpreter binary, fixed only on 3.13/3.14. 3.11 is in security-only
maintenance and 3.11.16 is its latest release, so they are excepted in `.grype.yaml` rather than
fixed. Moving the image (and `requires-python`/CI) to 3.12 or 3.13 removes the exception.

**Scope.** Bump the base image (re-pin the multi-arch index digest), run the full suite + `make
test-crewai` + `make mcp-floor` on the new interpreter (watch spaCy/torch/sentence-transformers wheel
availability), drop the `.grype.yaml` python-binary exception, and decide whether the published
wheel's `requires-python = ">=3.11"` stays (likely yes — the image and the library floor are separate).

**Outcome.** No CPython line is free of interpreter advisories: Grype's CPE matches record fixes only on
3.15 pre-releases. What differs is severity. 3.12.14 and 3.13.15 still carry a fixable **High**
(CVE-2026-82049, fixed in the 3.14 line); **3.14.7 has only Medium/Low**, below the release gate. So the
image moved to `python:3.14.7-slim`, chosen over 3.13, and the `.grype.yaml` exception was deleted rather
than kept. Fixable image findings: 4 Medium/Low, down from 10 with a High. Every runtime + `mcp` dependency
has a 3.14 wheel; the slim build compiles nothing but our own package. The full core suite passes on 3.14,
both image roles boot healthy, and an MCP round-trip works. CI runs the core job on 3.11 (wheel floor) and
3.14 (image). `requires-python` stays `>=3.11`; ruff/mypy keep targeting 3.11 so the floor stays honest.

---

## GX — Ideas taken from Graphiti (from the 2026-10 comparison, DESIGN §23 rev 97)

Three Graphiti designs aimed at our weakest LongMemEval types (stratified-90, 2026-10-03:
preference 0.267, multi-session 0.533, temporal 0.600, knowledge update 0.667; overall 0.633).
We take the designs, not Graphiti's synchronous per-episode LLM pipeline (~$5 per question
measured). Any LLM work runs batched in Dream State or in the opt-in extraction tier.

### GX-1 — Entity summaries in the retrieved context  🔄 (code in #266; benchmark deferred)

**Built.** `RETRIEVE_ENTITY_SUMMARIES` / `opts.entity_summaries` (default **off**) appends up to 5
Dream State entity summaries for the entities the hits mention, within 20% of
`max_memory_tokens`. Also capped Dream distillation input (`DREAM_DISTILL_MAX_MENTIONS`, 20); it
was unbounded.

**Deferred, to run after the current tasks:** the paired benchmark that decides the default.
```
arango-memory-longmemeval lme.json --rerank --extract --dream \
  --rerank-scoring replace,replace+entities --concurrency 8 \
  --checkpoint bench_runs/gx1/run.jsonl
```
- **Cost:** ~$25–35 in Haiku (Dream summaries ~250k input tokens/question with the cap, plus
  answer and judge for both variants) and ~1h. This is a user spend decision.
- **Decision rule:** flip the default on if `replace+entities` beats `replace` on accuracy
  (paired McNemar) without losing on single-session types. Record the result in DESIGN §23.
- **Expectation:** modest. About half of the 11 preference misses are the answerer saying "I
  don't know" with the relevant memory already in context. That is an answer-prompt issue entity
  summaries can't fix, and worth its own look.

**Run (2026-10-31, DESIGN §23 rev 103):** 90 questions on Haiku 5.5, about $4. `replace+entities`
scored **0.700** against 0.644 (+7/−2, p = 0.18), with gains in four types and no single-session
losses. That is not significant, so the default stays off for now. A run's answer-to-answer
scatter is about ±3–4 questions. Next: repeat answer passes with the new `--answer-only` flag on
the same ingest to measure that noise floor, then decide.

**Held-out re-run (2026-11-11, DESIGN §23 rev 104):** the +7/−2 did not reproduce. On 91 fresh
questions under the fixed harness, `replace+entities` averaged 0.861 against 0.875 over three
answer passes (+9/−13 pooled), inside the run-to-run scatter. The default stays off.

### GX-2 — Memory-level fact supersession + `as_of` retrieval  🔄 (built; off by default)

**Decision (2026-10-04):** the unit is the *memory*, not an extracted fact. Our typed
relations are four generic labels (`associated_with`, …), so "Sam associated_with Boston" and
"… Denver" don't contradict and there is nothing at the relation level to supersede. Atomic
facts fold into GX-3, which needs a fact store anyway. Design and measured cost: DESIGN §12.
Validation: tests plus the free knowledge-update ordering metric (`--retrieval-only`); a paired
accuracy run waits with the GX-1 benchmark.

**Measured (2026-10-04, DESIGN §23 rev 98):** knowledge-update newest-above-stale went from 0.614 to
**0.659** (+2/−0 of 44, p = 0.50), with recall unchanged, for about $2.50. The limit is detection: 9 of
16 missed stale/new pairs share no spaCy entity. Follow-up: a similarity-only candidate gate.

**Similarity gate (2026-10-09, DESIGN §23 rev 99):** `SUPERSESSION_GATE=similarity` is built. On Haiku
5.5 it reached only 0.659 (entity gate: 0.636), from a baseline of 0.614, although it offered twice
the pairs. The cause is a reply-format bug: with a bare-number reply format and a 32-token cap, Haiku 5.5
explains in prose and runs out of tokens before the number. An explain-then-`ANSWER:`-line prompt
links 13 of 15 missed pairs in a probe. Next: adopt it with strict parsing, re-run both gates, and
check the links for false positives.

**Explain-then-answer adopted (2026-10-30):** the verdict prompt now ends with a strictly parsed
`ANSWER:` line (256-token cap), and unparseable replies link nothing.

**Re-run (2026-10-30, DESIGN §23 rev 100):** newest-above-stale went from 0.614 to
- **0.773** with the entity gate (+7/−0 of 44, p = 0.016);
- **0.864** with the similarity gate (+11/−0 of 44, p = 0.001).

Recall held in both runs. A hand-judged sample of links puts precision at about 12/14 for the
entity gate and about 6/15 for the similarity gate (re-judged on full text in rev 101); the false
links share a topic but not a fact.
The default stays off. Next:
- a "same attribute, changed value" verdict;
- a full-mix paired accuracy run (with GX-1) to price false links on the other question types.

**Same-fact verdict (2026-10-30, DESIGN §23 rev 101):** the prompt now requires the same fact
about the same thing, a stated or clearly implied new value, and nothing for a shared topic alone.
The cap rose from 256 to 640 tokens, because with 4–5 candidates the 256 cap cut off about one
reply in ten. In a labelled in-context replay, precision went from 0.79 to 0.88 and recall from
0.79 to 0.82. On the KU re-run nothing measurable changed: 0.773 for the entity gate and 0.864
for similarity, the same as before. Similarity-gate precision is still about 4 in 13, so prompt
wording is exhausted as a lever. Next options are structural: a higher similarity floor, fewer
candidates, or requiring a shared entity or value. The entity gate (about 0.8 precision) remains
the default candidate.

**Anchored gate (2026-10-31, DESIGN §23 rev 102):** `SUPERSESSION_GATE=anchored` keeps similarity
candidates only when they share an entity or both contain a number. Applied to the existing links,
it keeps 50 of 60 gold links and 125 of 251 others; hand-labelled precision rises from about 0.36
to about 0.57. A cosine floor cuts gold and false links alike.

KU run: newest-above-stale went from 0.614 to **0.818** (+9/−0 of 44, p = 0.004), with 99 links
and sampled precision of 12/15. For comparison:
- entity gate: 0.773, 71 links, 12/15;
- similarity gate: 0.864, 155 links, 4/13.

That makes the anchored gate the candidate if supersession is turned on. Next: the full-mix paired
accuracy run, with GX-1.

**Full-mix run (2026-10-31, DESIGN §23 rev 103):** with the anchored gate, `replace+supersession`
scored 0.656 against 0.644 (+2/−1). Knowledge-update went from 0.800 to **0.933**. The one loss
was a multi-session question, and it came from answerer arithmetic over identical context, not a
false link. The default stays off (see the held-out validation below).

**Harness assessment (2026-11-11, DESIGN §23 rev 104):** the rev 103 numbers were held down by
the answer step, not retrieval. With a LongMemEval answer prompt, the official per-type judge and
a top-20, 8,000-token context, the same ingest scores 0.844 (0.644 before).

**Held-out validation (2026-11-11, same section):** on 91 fresh questions, 13 of them abstention
questions, the fixed harness scores 0.875 (mean of three answer passes) against 0.747 for the old
one, and declines 12 of 13 unanswerable questions. Under it, neither GX-1 nor GX-2 shows an
answer gain: every variant is within the run-to-run scatter of `replace` (the same variant
re-answered changes on 2–11 questions). Supersession still orders knowledge updates (7 → 10 of
13), but knowledge-update answers are already 13 of 13 without it. Both defaults stay off.

The remaining gap is multi-session questions (about 0.65). On the held-out sample about half of
their misses lack evidence and half are counting errors over complete evidence. Candidate items,
in order:
- round and neighbour expansion — built as LM-1 below; it helps single-session questions and
  not multi-session ones;
- fact-augmented keys;
- graph enumeration for counting questions — tried as a reader change, no gain (rev 107);
- event-date resolution;
- a Dream-maintained observation log.

Original scope note:

Graphiti stores facts with valid/invalid times, and a contradicting fact closes the old one.
RQ-3 found a global newness prior hurts temporal reasoning, but that a newness signal
scoped to conflicting statements of the same fact is what knowledge-update needs. That is this
mechanism. Today supersession is entity-level, decided in Dream State
(`lifecycle/conflict.py`). Scope: typed relations as facts with valid-from/valid-to;
same-fact contradiction detection in Dream State; newest-first only among conflicting facts;
an `as_of` retrieve filter. Measure with the RQ-3 ordering metric plus accuracy.

### LM-1 — Neighbour expansion  🔄 (built; off by default)

From the LongMemEval assessment (DESIGN §23 rev 104), not from Graphiti. Retrieval ranks single
turns; the answer to a retrieved question is often in the next turn.

**Built.** `RETRIEVE_NEIGHBOR_TURNS` / `opts.neighbor_turns` (default 0) injects the turns
within that many positions of each hit, in the hit's session, from the token budget the hits
left. Memories now store `turn_index`; the feature needs it with a `session_id`. The harness
stores each chat as its own session and has a `+neighbors` variant.

**Measured without an LLM (DESIGN §23 rev 105).** With ±1 turn, questions with all evidence in
context go from 70 to 73 of 79 (held-out) and 75 to 79 of 90 (original), for about two thirds
more context. Multi-session barely moves (11 → 11 of 14, 8 → 9 of 15). On the held-out sample
none of the ten stable misses gains evidence.

**Open.**
- Answer accuracy: three `--answer-only` passes of `replace,replace+neighbors` on both ingests,
  about $1.20. This is a user spend decision.
- Whether to turn it on by default depends on that run and on the extra context being worth it.
- Whole-session expansion reaches more multi-session evidence but needs about 18,000 tokens.

### FK-1 — Fact-augmented keys  🔄 (built; off by default)

From the LongMemEval assessment (DESIGN §23 rev 104), after LM-1. A turn is indexed under what it
is mostly about, so a fact it states in passing ("by the way, I baked a baguette last Saturday",
inside a request for bread tips) ranks far below the topic. A free diagnostic over both samples
(DESIGN §23 rev 106) found 17 of the 31 evidence turns missing from the top-20 context at pool
rank 50–150; widening the reranker window does not recover them.

**Built.** `FACT_KEYS=true` makes Dream State extract each memory's facts (one LLM call per
memory; `FACT_KEYS_PREFIX=user:` mines only the user's turns) and store them with one embedding
per fact. `RETRIEVE_FACT_KEYS` / `opts.fact_keys` adds a BM25 arm and a best-fact-cosine arm; the
memory is still what is returned. Harness variant `+facts`.

**Measured (DESIGN §23 rev 106).** On 91 held-out questions, three answer passes: `replace` 0.879
against `replace+facts` 0.872 at fact-arm weight 0.3 (0.846 at 1.0, where two single-session-
assistant questions lost their evidence). Evidence recall@k 0.941 → 0.951, multi-session accuracy
unchanged; no pass is significant. Extraction costs about $8–12 per 91 questions. Stays off.

**Tried, not adopted (DESIGN §23 rev 107).** Using the stored facts for counting questions: the
remaining multi-session misses are reading errors over complete evidence, and a structured
counting prompt gained on the development sample but not on the validation sample. Needs several
hundred multi-session questions to settle, so it is closed as won't-do.

**Open.** An index for the fact-vector scan if the arms are ever enabled at scale.

### GX-3 — Fact retrieval arm

Graphiti searches short fact sentences rather than raw turns, which gathers scattered
multi-session evidence. Our GLiREL/Haiku extractors already produce typed relations, but only as
graph edges. Scope: render them as fact sentences, embed them, and add a fourth arm to the
fusion. Only active when an LLM or GLiREL extractor is configured, so the keyless default is unchanged.

### WQ-1 — Multi-worker write queue

**Problem.** The durable write worker (`ingest/worker.py`) is one daemon thread that commits one
intent at a time. On the hosted core (Railway + ArangoGraph) a commit takes about 2.6 s at p50
(1.5 s on a quiet core), so a burst of N async writes takes about N × 2 s to drain. Every `store`
caller that does not pass `sync: true` pays this, and `/v1/flush` can only wait for it.

**Evidence (Diligence Room, 2026-11-20).** One live run enqueues about 130 claims in under 4 s.
Both flush barriers hit their 30 s cap with the queue still full, so the red-team read partial
data, and the memo's `prime` call timed out against a busy core. The flush steps are 60 of the
run's ~90 s. Writing the claims synchronously instead (`sync: true`, 4 in flight per agent) made
it worse: each sync store also forces an ArangoSearch view sync, and ~16 in flight saturated the
core (`/health` itself timed out; the specialists and red-team all failed). So "more concurrency" is
not free, and the number has to be measured, not assumed.

**What the code already gives us**
- `ArangoQueue.claim` is one AQL `UPDATE … OPTIONS { exclusive: true }` that leases the oldest free
  intent, so two workers cannot take the same one. `InProcessQueue` is lock-protected.
- Commits are idempotency-keyed (`idx_idempotency` unique index), so redelivery after a lease
  expiry is safe.
- `pending_count(tenant)` counts every un-acked intent, so the flush barrier is already correct
  with any number of workers.
- Entities and steps have unique natural-key indexes (`idx_entity_natural_key`,
  `idx_step_natural_key`), so concurrent upserts converge on one record.

**What stops us from just starting N threads**
1. **One DB handle.** `ArangoQueue` shares one connection between request threads (enqueue) and
   the worker, serialised by `self._lock`; `WriteWorker` holds one more. N workers need N
   connections (a python-arango handle is not assumed thread-safe), and
   the queue's lock must not serialise the long `commit` calls. It only guards the short queue ops
   today, which is fine, but each worker should own its connection.
2. **Idle polling.** The loop polls every 50 ms when empty, which on the Arango backend is an AQL
   claim query 20 times a second per worker. N workers multiply that on an idle core. Use a
   condition variable / event for the in-process queue and exponential poll back-off for the Arango
   queue.
3. **Order is no longer FIFO per tenant.** What depends on commit order today:
   - `StepIntent.prev_step_key` chains a procedural trace; steps for one agent must commit in
     order, or the chain links to a step that does not exist yet.
   - Full mode: `_track_session_topic` is a read-modify-write on the `sessions` doc, and
     `_enforce_working_capacity` sorts by `created_at`. Concurrent turns of one session race.
   - Lite mode (the default) has neither, so it is safe to parallelise first.
   Ordering fix: shard workers by `(tenant_id, agent_id)` (hash to a worker) so one agent's writes
   stay serial while different agents run in parallel. That alone covers the Diligence Room (four
   specialists plus the red-team) and keeps step chains and sessions correct. A single agent's
   burst is still serial, which is the case batching (below) addresses.
4. **Unique-index conflicts.** Two workers upserting the same new entity can hit a unique-key
   conflict on one of them. `process()` already retries with backoff and dead-letters after
   `write_max_retries`, so the loser retries and converges. This needs a test, and the dead-letter
   count is the acceptance signal.
5. **Lease length.** `write_lease_seconds` is 60 s. A commit must always finish well inside it, or
   a second worker takes the intent while the first is still running (harmless but wasteful). Fine
   at 2.6 s, but worth alerting on commits over 30 s.
6. **Shared resources.** Workers compete with request threads for the DB connection pool, the
   embedding provider's rate limit, and (with a CPU extractor such as spaCy) the GIL. The sync
   experiment above is the warning.

**Step 0 — where the time goes. DONE (2026-11-20, #289 timers, read from the hosted core's
`/health` after a live Diligence Room run, n = 33 commits, p50).**

| stage | p50 | note |
|---|---|---|
| `write` (one claim) | 2865 ms | |
| `write.episode` | 177 ms | 2 DB calls |
| `write.memory` | 91 ms | 1 DB call |
| `write.embed` | 224 ms | the claim's embedding |
| `write.entities` | 2322 ms (81%) | |
| ↳ `edges` | 716 ms | 2 edge inserts per entity |
| ↳ `relate` | 555 ms | 1 DB call per entity pair |
| ↳ `upsert` | 379 ms | 1 DB call per entity |
| ↳ `embed` | 197 ms | entity-name embeddings |
| ↳ `candidates` | 185 ms | entity-resolution lookups |
| ↳ `match`, `extract`, `relations` | 12 / 0 / 0 ms | not a cost |

A single database call costs about 90 ms (`write.episode` is two calls at 177 ms, `write.memory`
one at 91 ms). A claim makes about 25 of them one after another, so roughly 2.3 s of the 2.9 s is
waiting on the network between the Railway core and ArangoGraph, and the embedding calls are the
other ~15%. Extraction and matching cost nothing (the extractor is cheap and local). The measured
drain rate agrees: the Diligence Room's flush lines showed 91 and 85 writes still queued after
30 s, about 0.35–0.4 writes/s.

Consequences for the design: the time is **round trips, not CPU**, so (a) threads overlap well
until the DB pushes back, and (b) the bulk path that already exists, `store_many` +
`write_entities_many` (IN-1/IN-2/IN-6: about 2 round trips for the records and a handful for the
whole graph pass, instead of about 3 + 4E + pairs per turn), attacks the cost directly and also
helps a single agent's burst, which sharding cannot. **So WQ-1b goes first.**

**Design (in order).**
- **WQ-1b — batch commit (first).** The worker claims up to `WRITE_BATCH_SIZE` intents in one queue
  round trip (`claim_many`), groups the batchable ones by `(tenant_id, agent_id)`, commits each
  group with one `store_many(..., extract=True)` call and acks the lot in one round trip. Batchable
  means a `WriteIntent` in lite mode with no `session_id` and `memory_type == "episodic"`:
  `store_many` skips full-mode prospective indexing, working-capacity eviction and per-session
  topic tracking, so anything that needs those stays on the per-intent `store()` path. If a batch
  fails, its intents fall back to per-intent `process()`, so retry, backoff and dead-lettering still
  isolate a bad intent. Default `WRITE_BATCH_SIZE=1` = today's behaviour. Documented differences of
  the bulk graph pass (DESIGN §8 / `write_entities_many`): semantic dedup of two differently named
  but similar **new** entities inside one batch is left to consolidation, and the edge weight folds
  once per batch.
- **WQ-1a — parallel agent groups (built behind a setting).** `WRITE_WORKER_CONCURRENCY` (default
  1 = serial). The worker still claims one round of up to `WRITE_BATCH_SIZE` intents, then splits it
  into one unit per `(tenant_id, agent_id)` and commits the units in parallel on a thread pool, each
  thread on its own DB connection. An agent's writes stay in order on one thread, and the round is
  acked only after every unit finishes. This replaced the hash-routing sketch: with four agents a
  stable hash into four workers collides about nine times in ten, so some agents would still drain
  one after another. A CPU-bound extractor is wrapped in a lock (its calls run one at a time while
  the database round trips overlap). Concurrent groups that write the same entity or edge can hit a
  write-write conflict or a unique-key violation (seen locally with four agents mentioning the same
  people); the batch retries up to three times before it falls back to per-intent commits. If any
  unit raises, the others finish first, then the whole round is released and retried (commits are
  idempotency-keyed).
- **Observability.** The `write.*` stage timers (#289). Add gauges for queue depth, oldest-intent
  age and worker utilisation on `/health`. The flush response already returns `pending`, and the
  Diligence Room logs it (#287).

**Expected effect (estimate, not measured).** With a batch of 16 the ~25 per-claim round trips
become roughly 8–10 per batch, so about 10–20× fewer round trips per claim. The embedding calls
batch too. 100 claims should then drain in well under the 30 s flush cap. Batching and a worker
pool multiply, so WQ-1a may not be needed for the Diligence Room.

**Acceptance (WQ-1b)**
1. A burst of 100 writes from 4 agents against the hosted topology drains in under 30 s with
   `WRITE_BATCH_SIZE=16`, and `/health` stays responsive (under 2 s) while it drains.
2. Memory and episode counts equal a one-by-one run of the same burst, and entity counts match
   except for the documented intra-batch semantic-dedup difference; zero dead letters.
3. `WRITE_BATCH_SIZE=1` behaves exactly as today (existing worker and queue tests pass unchanged).
4. Unit tests: `claim_many` is FIFO and leases once; `ack_many` clears the lease; a batch of
   same-agent intents commits through one `store_many` call; a failing batch falls back per intent
   and still commits the good ones; session-bound and non-episodic intents stay on `store()`.

**Acceptance (WQ-1a, if built).** A burst of 200 writes from 4 agents drains in under 60 s with
`WRITE_WORKER_CONCURRENCY=4`; counts equal a single-worker run; zero dead letters; a step chain
A→B→C never has a dangling `prev_step_key`; sharded routing is stable; two workers never commit the
same intent; shutdown releases leased intents; an injected unique-key conflict retries and
converges.

**Rollout.** Ship with the defaults at 1. Set `WRITE_BATCH_SIZE=16` on the Railway core, run the
Diligence Room live, and read the flush log lines (`drained` or `N still queued`), the step timings
and `/health` `write.*`. Remove the demo's flush-cap workarounds only after that run is clean.

**Size.** WQ-1b: M (about 200 lines plus tests). WQ-1a: M, only if the batch result says so.

---

## PF-1 — Request latency: concurrency, payload and round trips

**Why.** Measured 2026-11-25 on `bench_fresh` (a 565-memory tenant, 1536-dim vectors) through a
TCP proxy that adds the ~90 ms per database call that WQ-1 measured between Railway and
ArangoGraph. Python's own CPU is about 5% of a request: 51 ms of a 1.1 s retrieve, and 26 ms of
spaCy in a 1.5 s write. The rest is waiting on the database and the embedding API, one call after
another. That is also why a rewrite in another language is out of scope (see the end).

| Finding | Measured |
|---|---|
| Every route handler is `async def` but calls python-arango and the embedding API synchronously, and the image runs one uvicorn process. The core serves one request at a time. | 8 concurrent retrieves: 7.1 s (1.3 s with the handlers in the threadpool). `/health` during them: 7.0 s (0.2 s). |
| Every retrieval arm returns each row's embedding. Nothing reads them at the default `mmr_lambda=1.0`. | 5.6 MB per retrieve with them, 0.18 MB without |
| `vector_check` asks the database whether the vector index exists on every retrieve. On ArangoGraph, where vector indexes are not allowed, that is up to 4 calls, including a failed `add_index` once there are 2,560 memories. Entity resolution does the same on `entities` for every write. | 96 ms p50 |
| The access write (`reset_access`) runs before the response is sent. | 101 ms p50 |
| A one-by-one write makes ~25 database calls and 2 embedding calls. The batch path exists but is off (`WRITE_BATCH_SIZE=1`). | 1.5 s per write. 24 writes: 42.6 s one by one, 3.9 s in batches of 12. |

Retrieve stage p50s: vector 343, bm25 187, graph 139, access 101, vector_check 96 ms. Embed: 0 ms
when cached, up to ~700 ms on a miss.

**On the hosted core** the vector arm is off, so a retrieve today runs these steps in a row:
1. Embed the query. The vector is unused, because the vector arm is off.
2. bm25 (1 call).
3. vector_check (1–4 calls).
4. graph (1 call).
5. access (1 call).

PF-1a, c, d and e leave only bm25 → graph.

### PF-1a — Move the handlers off the event loop (PR 1)  ✅ #303

- **Handlers.** Route handlers that touch the database, embedder, generator or extractor become plain
  `def`. FastAPI then runs them in its threadpool (anyio, 40 threads). `/v1/flush` stays `async`,
  because it sleeps between polls, and runs `pending_count` and `force_view_sync` through
  `anyio.to_thread.run_sync`.
- **Database handles.** python-arango's docs say its `requests.Session` "may not always be
  thread-safe", and this codebase already gives each thread its own connection (the write worker,
  `test_concurrency`). Request threads therefore get handles from a small pool of leased handles,
  each with its own `ArangoClient`.
  - Built as `DatabasePool.lease()` (`ArangoMemoryClient.pool()`) in `client.py` and passed to
    handlers by a `get_db` dependency. This replaces about 20 `client.db` uses in `api/app.py`.
  - Why a pool rather than `threading.local`: anyio retires a thread after 10 s idle, so
    per-thread clients would reconnect (TCP + TLS to ArangoGraph) after every quiet spell.
  - Size: new setting `ARANGO_POOL_SIZE`, default 16. When every handle is out, a request waits
    for one.
  - `/health` and `/ready` keep a handle of their own, so they never wait behind load.
- **Write queue.** `ArangoQueue` drives one shared connection under a `threading.Lock`. Once
  handlers run in parallel, every `enqueue` (~90 ms) would queue behind the others and behind the
  worker's claims. Each queue call should lease a handle instead, and the lock goes. Claims stay
  atomic in the database (`OPTIONS {exclusive}`).
- **Shared state the event loop used to serialise.** Audit each piece and lock it where needed:
  - `embedding_cache`: an `OrderedDict` LRU with no lock. Concurrent `move_to_end` / `popitem`
    can raise.
  - The extractor on the `sync` write path: spaCy would run in request threads and the worker at
    once. Wrap it in the existing `_SerializedExtractor` unless it is I/O-bound.
  - `QueryCache`: only its hit counters can race, which is harmless.
  - `LatencyRecorder` and the rate limiter already lock.
- **Measurement script.** Commit the analysis's delay proxy and load test as
  `core/scripts/latency_bench.py`: proxy, concurrent retrieves, `/health` probe, and stage p50s
  from `/health`. Every PF PR then reports the same numbers.
- **Tests.**
  - A structural test: no route endpoint is a coroutine, except an allowlist (`/health`,
    `/ready`, `/v1/flush`, `/v1/store`, `/v1/step`).
  - A behaviour test with a patched `retrieve` that blocks: two concurrent retrieves must overlap
    on different handles, and `/health` answers while they run.
  - The existing API, sync/flush and concurrency tests pass unchanged.
- **Acceptance.** On the 90 ms proxy: 8 concurrent retrieves in ≤ 1.5 s wall, and `/health` p95
  under that load < 300 ms. Single-request latency unchanged within 10%.
- **Size.** M: ~150 lines plus tests, most of it the mechanical handle change.
- **Result (2026-11-25, 90 ms proxy, `latency_bench.py`).** 8 concurrent retrieves: 1.19 s wall
  (median of 5 rounds, max 1.37 s). `/health` under load: p95 231 ms (idle 198 ms). A single
  retrieve: 804 ms p50.
- **Built beyond the scope above.** `/v1/store` and `/v1/step` stay `async` and push their calls to
  a thread, so a queued write holds no database slot. The wait for a connection happens on the event
  loop, so it holds no thread. Inline commits run one at a time, and so do the maintenance routes
  (they were never written to race each other). `reset_access` ignores write conflicts.

### PF-1b — Turn on batch writes on Railway (config, no PR)  ✅ on at 64

- **Status.** The owner set both variables on the Railway core (reported 2026-11-26). At batch 16
  the flush did not get faster; at 64, the same day, it took 8.3 s, and 7.8 s after #311
  (results below). Closed on 2026-11-26 with no dead letters; the flush is faster than the 14.8 s
  baseline but short of the "few seconds" first expected.
- Set `WRITE_BATCH_SIZE=16` (now 64, see below) and `WRITE_WORKER_CONCURRENCY=4` (one per
  Diligence Room specialist) on the Railway core. Both are built and tested (WQ-1b #291, WQ-1a #301) and off by default. With
  spaCy, the worker already serialises extraction across groups.
- **Who.** The owner, in the Railway dashboard (no Railway CLI on the dev machine). This can happen
  before PF-1a.
- **Verify.** Run the Diligence Room live and check:
  - `flush:specialists` (14.8 s on 2026-11-20) drops to a few seconds;
  - `/health` shows the `write_many.*` timers;
  - there are zero dead letters.

  Then follow WQ-1's rollout note: remove the demo's flush-cap workarounds only after a clean run.
- Whether to make 16 / 4 the defaults in `config.py` is a separate decision.

**Result (2026-11-26, one live run on a hard-reset room).** Verdict "pass", 10 contradictions,
about 100 claims.

| step | 2026-11-20 | 2026-11-26 |
|---|---|---|
| specialists (slowest of 4) | 3.8 s | 4.5 s |
| `flush:specialists` | 14.8 s | 16.6 s, drained |
| consolidate | 0.6 s | 0.7 s |
| red-team | 12.6 s | 7.6 s |
| `flush:redteam` | 4.2 s | 4.6 s, drained |
| synthesis | 20.0 s | 12.3 s |

| check | result |
|---|---|
| `write_many.*` timers on `/health` | yes: 11 batched commits, about 9 claims each |
| `flush:specialists` drops to a few seconds | no |
| `/health` responsive | 184 checks over 7 minutes, no errors, p95 345 ms, max 1.3 s |
| zero dead letters | not checked (no access to the Railway logs) |

The red-team and synthesis changes can't be credited to this work: model response times are part
of both.

Why the live flush was no faster than the 14.8 s baseline (inferred): that baseline probably
already had batching on, since at the one-by-one rate WQ-1 measured (about 0.4 writes/s) 104
claims could not have drained that fast. So "a few seconds" assumed too much.

**Local replay (2026-11-26).** About 100 claims from four agents arriving over 4.5 s, as in the
live run, into the `arango` queue and write worker through the 90 ms delay proxy: OpenAI
embeddings, `fake` extractor, scratch database. Drain time runs from the first write to an empty
queue, two runs per setting. The harness was a one-off script and is not in the repository.

| `WRITE_BATCH_SIZE` | `WRITE_WORKER_CONCURRENCY` | drain |
|---|---|---|
| 16 | 1 | 30.0–31.1 s |
| 16 (hosted setting) | 4 | 16.4–16.8 s |
| 32 | 4 | 11.1–11.7 s |
| 64 | 4 | 7.5–7.7 s |
| 128 | 4 | 7.4–7.7 s |

- **The batch size is the limit.** A batched commit took about 1.4 s whether it held 2 claims or
  11. A round claims at most `WRITE_BATCH_SIZE` and splits them by agent, so 16 means many rounds
  of small commits: 8 rounds at 16, 3 at 64.
- **Concurrency works.** 4 roughly halved the drain, and each round held two to four agents. This
  corrects the first reading of the live run, that parallel commits helped little.
- **The first claim goes alone.** An idle worker takes the first write by itself and commits it
  one by one, about 2.4 s before the next round can start. A short wait before claiming could
  fix this (code, not built).
- No dead letters. Some runs had one or two batch calls fail with a write conflict (error 1200)
  and "succeed" on retry. That retry skipped the entity step, so those batches lost their entity
  links: a bug, since fixed (graph writes now retry in place).
- Hosted batches are slower (about 2.2 s against 1.4 s), so expect a longer drain there.

**Hosted run at 64 (2026-11-26, hard-reset room, concurrency 4).** The owner set
`WRITE_BATCH_SIZE=64`. Verdict "pass", 11 contradictions, 104 claims.

| step | batch 16 | batch 64 |
|---|---|---|
| specialists (slowest of 4) | 4.5 s | 3.5 s |
| `flush:specialists` | 16.6 s | 8.3 s, drained |
| consolidate | 0.7 s | 0.6 s |
| red-team | 7.6 s | 7.9 s |
| `flush:redteam` | 4.6 s | 4.6 s, drained |
| synthesis | 12.3 s | 10.4 s |
| batched commits | 11 | 7 |

`/health`: 67 checks over 150 s, no errors, p95 329 ms, max 448 ms. One run at each setting.
The flush is now below the 14.8 s baseline but not yet "a few seconds". A batched commit on
the hosted core takes seconds (the `write_many` stage p50s sum to about 2.8 s), so the next
gain would come from fewer database calls per commit.

**Fewer calls per batch (✅ #311).** Episodes, memories and the replay check are one statement, and
the three edge collections another: 8 database requests per batch become 4 (plus one when a batch
merges into existing entities). Both statements were checked on a local cluster, in a OneShard
database and in one with collections on different DB servers: same graph as before, and a write
conflict in one collection left nothing behind in the others. Local replay through the 90 ms
proxy, three runs each:

| `WRITE_BATCH_SIZE` | drain before | drain after | per batch commit |
|---|---|---|---|
| 16 | 16.6–17.4 s | 13.9–15.2 s | about 1.4 → 1.0 s |
| 64 | 7.7–9.2 s | 6.9–8.5 s | about 1.7 → 1.3 s |

**Hosted run after #311 (2026-11-26, hard-reset room, batch 64, concurrency 4).** Verdict
"pass", 11 contradictions, 102 claims, 7 batched commits, both flushes drained, 342 relationship
edges in the room's graph. One run, against the earlier run at 64.

| | before | after |
|---|---|---|
| `flush:specialists` | 8.3 s | 7.8 s |
| `flush:redteam` | 4.6 s | 4.8 s |
| record turns (`existing` + `insert` → `insert`), p50 | 99 + 667 ms | 549 ms |
| write edges (`edges` + `relate` → `edges`), p50 | 200 + 177 ms | 193 ms |
| `write_many.entities.pool` p50 | 637 ms | 559 ms |
| `write_many.entities` p50 | 1673 ms | 1547 ms |

`/health`: 67 checks over 150 s, no errors, p95 345 ms, max 426 ms. Excluding the embedding
calls, the stage p50s add to about 0.3 s less per batch. The flush difference is too small to
show from one run each. Dead letters and write conflicts: see the log check below.

The largest cost left in a batch is the entity step (about 1.5 s). The candidate fetch within it
downloads the tenant's entity embeddings on every batch (PF-1h).

**Dead letters (2026-11-26): none.** The Railway deploy logs of the day's nine deployments
(12:10–19:51 UTC, all three live runs, 341 `/v1/store` requests, all 200) have no
`write dead-lettered`, `batch commit failed`, `batch hit a write conflict`,
`graph write still conflicting` or `write worker iteration failed` line, and no other warning or
error from the core. So no run lost entity links to the conflict bug fixed in #310. The logs
don't show whether concurrency 4 is in effect.

**Follow-ups (not part of this item).**
- Remove the demo's flush-cap workarounds, which waited for a clean run.
- The hosted core appears to run the `fake` extractor: extraction took 0.2 ms and every entity is
  a `Concept`, including words such as "No" and "Dec". `EXTRACTION_PROVIDER` is not set on Railway
  (owner, 2026-11-26), and the image installs no extraction extra, so `spacy` needs an image change
  first.

### PF-1c — Stop returning embeddings from the retrieval arms (PR 2)  ✅ #304

- Remove `embedding: doc.embedding` from `_BM25_QUERY`, `_FACT_BM25_QUERY`, `_FACT_VECTOR_QUERY`,
  `_VECTOR_QUERY`, `_GRAPH_QUERY` and `_SUCCESSORS` in `retrieve/search.py`.
- `_mmr` is the only reader. When `lambda < 1.0`, load the fused candidates' embeddings by key in
  one query before MMR. At 1.0, fetch nothing.
- **Tests.**
  - Arm rows carry no `embedding`.
  - At λ=1.0, results for a fixed query set (keys and order) are identical to before.
  - `test_mmr` passes, and a λ=0.5 retrieve still diversifies.
- **Acceptance.**
  - ≤ 0.3 MB per retrieve on bench_fresh (was 5.6 MB).
  - Lower bm25 and vector p50s. Through the proxy: bm25 154 → ~122 ms and vector 194 → ~156 ms.
    The proxy adds no bandwidth limit, so a real WAN saves more.
- **Optional, measure first.** `orjson` as python-arango's `deserializer`. After this item, read
  payloads are small, so it only matters on the write path (PF-1h).
- **Size.** S.

### PF-1d — Cache the vector-index state, and skip the query embedding when it is unused (PR 2)  ✅ #304

- **Cache** (`schema/collections.py`):
  - Cache `has_vector_index` per (database, collection) for 60 s.
  - Cache a below-threshold "deferred" answer for the same 60 s, which also drops the `count()`
    call.
  - After a failed `add_index` (`IndexCreateError`), wait 10 minutes before trying again, and log
    the failure once.
  - `drop_vector_index` and a successful `add_index` clear the entry.
  - Guard the cache with a lock.
- **Readers.** The cache serves retrieval's `vector_check`, entity resolution's `_use_ann` on every
  write, and `/health`.
- **Skip the embedding.** In `_retrieve_impl`, read the cached state before embedding the query.
  If the vector arm is off and fact keys are off, don't embed (lite mode only; HyDE and multihop
  are unchanged). On ArangoGraph this removes an embedding API call (~300–700 ms) from every
  uncached query.
- **Tests.**
  - Two retrieves make one index lookup.
  - A patched `add_index` that raises is attempted once per backoff window.
  - After a drop, the next retrieve sees no index (the existing `test_vector` cases).
  - A perf invariant: no embed call when the vector arm is off.
- **Acceptance.** `vector_check` p50 < 1 ms once the cache is warm.
- **Size.** S.

### PF-1c + PF-1d result (2026-11-25, `bench_fresh`)

Against `main` (with PF-1a), same session, 90 ms proxy for the timings:

| | `main` | PF-1c+d | Target |
|---|---|---|---|
| Database bytes per retrieve (median of 100) | 6.66 MB | 0.31 MB | ≤ 0.3 MB |
| `bm25` p50 | 238 ms | 121 ms | ~122 ms (from 154) |
| `vector` p50 | 329 ms | 263 ms | ~156 ms (from 194) |
| `vector_check` p50 | 97 ms | 0.0 ms | < 1 ms |
| Single retrieve p50 | 794 ms | 578 ms | |
| 8 concurrent retrieves, wall p50 | 1099 ms | 811 ms | |
| `/health` p95 under that load | 235 ms | 114 ms | |

The byte target was missed by 0.01 MB (max 0.44 MB). The stage p50s are taken under concurrent
load, so compare the two columns with each other, not with the scoped numbers; `vector` did not
reach its scoped value. Top-k results over 100 queries differ between `main` and this change in 5–6
queries, and between two runs of the same code in 6–11; every query that differed across versions
also varied between runs of one version. The query-embedding skip is covered by tests, not by these
timings (the index is trained and the queries cached).

As built: `ensure_vector_index` stays uncached (ops and tests); request paths call
`vector_index_ready`. If a cached "ready" is wrong because another process dropped the index, that
retrieve clears the cache and answers without the vector arm.

### PF-1e — Fewer round trips per request (PR 3)  ✅ #305

1. **Write access after the response.** The API passes an `access_sink` to `retrieve` / `prime`,
   and the handler runs `reset_access` as a FastAPI background task on its own leased handle.
   Library callers (eval, LangChain, CrewAI) keep today's synchronous write. This saves one round
   trip per retrieve. The trade-off: an access update can land a few ms after the response, or be
   lost if the process dies first. That is acceptable for a decay signal.
2. **One embedding call per one-by-one write.** `store()` embeds the turn, then `write_entities`
   embeds the entity names in a second call. Instead, run extraction first and embed
   `[content, *names]` in a single `embed_batch_cached` call. Keep today's failure behaviour: if
   extraction fails, the memory is still stored. This saves one embedding round trip (~250 ms) on
   `sync` writes and when `WRITE_BATCH_SIZE=1`. `store_many` already batches.
3. **Run BM25 and vector in parallel.** When the vector arm is on, run the BM25, vector and fact arms
   at the same time, each on its own leased handle. Graph still waits for their seeds. This only
   helps self-hosted deployments, because ArangoGraph has no vector arm today. Library callers
   without a pool keep the sequential path.

- **Tests.**
  - Perf invariant: one embed call per write (`embed_calls == 0`, `batch_calls == 1`).
  - With the vector arm on and a patched delay, a retrieve's arm time is about the max of the
    arms, not their sum.
  - `access_count` still increments (the test waits for the background task).
- **Acceptance.** On the proxy: retrieve p50 ≤ ~0.5 s with the vector arm on (from 1.1 s), and
  one-by-one write p50 down by ~250 ms.
- **Considered and deferred.** Merging BM25 and graph into one AQL query would save one more round
  trip on ArangoGraph, but it loses per-arm failure isolation and per-arm stage timings. Revisit
  once the hosted numbers are in.
- **Size.** M.
- **As built.**
  - The access write runs as a background task on the request's own handle, not a second lease.
    From FastAPI 0.118 `get_db` releases after background tasks, so a second lease would have let
    requests that hold every handle wait on each other.
  - The parallel arms use a separate pool and thread pool (`ParallelArms`, up to
    `ARANGO_POOL_SIZE` connections), for the same reason. The first arm runs on the request's
    handle.
- **Result (2026-11-26, 90 ms proxy, `bench_fresh`, vector arm on).** Two runs of `main` and four
  of this change; queries were embedding-cached.

  | | `main` | PF-1e | Target |
  |---|---|---|---|
  | Single retrieve p50 | 596–604 ms | 344–406 ms | ≤ ~500 ms |
  | 8 concurrent retrieves, wall p50 | 726–815 ms | 446–785 ms | |
  | One-by-one write p50 (24 writes) | 1181–1208 ms | 989–1064 ms | ~250 ms less |
  | 24 one-by-one writes, total | 37.3–37.8 s | 33.3–34.4 s | |

  The write target was missed: the p50 fell about 160 ms. Only 16 of the 24 test turns had
  entities, and only those made a second embedding call. Concurrent wall time is noisy; one run
  had a 2.6 s worst round (main's worst was 0.9 s), probably first-use connection setup in the arm
  pool.

### PF-1f — Put the core next to the database (ops, the owner)

- **Measure the round trip from inside the Railway container.** Run
  `python -m arango_memory.check` from Railway's shell. It writes and then deletes one probe memory
  under an isolated tenant.
- **Compare regions.** Check the Railway service region against the ArangoGraph deployment's cloud
  region. If they differ, move one to match. A write makes ~25 calls and a retrieve 2–6, so the
  distance multiplies everything else.
- **Check two settings in the ArangoGraph console:**
  - the deployment model: OneShard runs each AQL query on one DB server, while sharded adds
    coordinator ↔ DB-server hops;
  - whether its ArangoDB version now allows vector indexes. The hosted vector arm has been off
    since June.
- No code.

### PF-1g — More processes (later, only if PF-1a–e are not enough) (PR 4)

- **When.** Python CPU per retrieve is ~51 ms, so one process tops out around 20 retrieves/s on
  the GIL. Add processes only if hosted load approaches that.
- **How.** Set `WEB_CONCURRENCY=N` (uvicorn reads it, so the Dockerfile doesn't change), or add
  Railway replicas.
- **Prerequisites, in the same PR:**
  - `WRITE_QUEUE_BACKEND=arango` (already set on Railway). With the in-memory queue, each process
    has its own queue, so `/v1/flush` on one process can't see writes queued on another. Startup
    refuses `WEB_CONCURRENCY > 1` with the memory backend.
  - A migration lock. When two processes start together, both run migration 1, and one fails
    startup with a 409 lock timeout (seen 2026-11-25). `run_migrations` should take a lease on the
    version document, and the other process wait until the version is current.
  - Rate limits are per process unless `REDIS_URL` is set. The query cache is per process, which
    is fine.
- **Docs.** The `ops.md` scaling section.
- **Size.** S–M.

### PF-1h — Entity resolution fetches the tenant's entity vectors on every write (found while scoping; measure first)

- **What happens.** Without the entity ANN index, `write_entities` / `write_entities_many` resolve
  against `_FETCH_EXISTING`, which returns every entity in the tenant with its embedding, once per
  write (or per batch). On ArangoGraph there is never an ANN index, so this always happens. The
  payload grows with the tenant: 2,000 entities at the default 256 dims is about 10 MB per write.
- **Not measured yet.** The 2026-11-25 ingest test used a fresh database.
- **Measure first.** Read `write.entities.candidates` on `/health` for the Diligence Room tenant,
  and that tenant's entity count.
- **Fix, if the cost is large.** Score in AQL instead: `COSINE_SIMILARITY` against each new name's
  vector, top-k per name. Return keys, names, labels and scores only, and have `_best_match` take
  the score instead of recomputing it.
- **Size.** M.

**Order.**
- PF-1b now (config only).
- In code, PF-1a first.
- PF-1c and PF-1d next, together in one PR. They don't depend on PF-1a, but they are measured on
  top of it.
- PF-1e after PF-1a, since it needs the leased handles.
- PF-1f any time.
- PF-1g and PF-1h only if their measurements call for them.

**Expected end state (estimate, not measured).**
- Hosted retrieve: two round trips (bm25 and graph), no query embedding, no index check, and the
  access write after the response. Roughly 0.3–0.4 s at 90 ms per call, down from ~0.5–1.5 s.
  Concurrent requests no longer queue behind each other.
- Hosted writes: set mostly by the batch size (PF-1b) and the region (PF-1f).

**Out of scope.**
- **A Rust rewrite.** CPU is ~5% of request time, and a rewrite would mean leaving spaCy,
  sentence-transformers and the model SDKs behind.
- **The cross-encoder reranker's 3.3 s on CPU** for 50 candidates. It is off by default and not in
  the image, so it is a separate decision if production turns it on.
