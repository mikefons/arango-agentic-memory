# The Due-Diligence Room — a multi-agent showcase (design + roadmap)

**Goal:** a *new* reference demo (not the dungeon) that proves what
`arango-agentic-memory` is for — a **shared, bi-temporal, contradiction-aware memory**
that many specialist agents read and write over a long-running investigation. The
business process is **investment / M&A due diligence**: specialist agents interrogate a
target company's data room, disagree, correct each other over time, and hand off to a
red-team and a synthesizer that produce an **investment memo with an evidence chain**.

> **The thesis as a business rule:** the answer isn't in any one document or any one
> agent's context — it lives in the *reconciled, time-aware, corroboration-weighted*
> memory of everything the team found. Kill the memory layer and the process can't tell a
> superseded claim from a current one, a rumor from a filing, or a contradiction from a fact.

Companion to [GUILD.md](GUILD.md) (the game demo) and the MA-* multi-agent roadmap in
[ROADMAP.md](ROADMAP.md). Where the Guild teaches handoff through *play*, the Diligence
Room teaches it through a *high-stakes knowledge process*.

---

## Why this demo (and why it's hard elsewhere)

Vercel Workflows + `eve` (and LangGraph / CrewAI / Swarm) give you durable orchestration
and agents. **None of them give you the store this process needs:** one where facts
*conflict*, *change over time*, arrive from sources of *unequal trust*, and the answer
requires *reasoning over a graph of accumulated evidence produced by many agents*. That is
exactly the memory core's differentiators, and exactly where generic RAG ("documents in a
vector DB") and stateless agent swarms fail:

| The process needs… | The core provides (already built) |
|---|---|
| Many agents contributing to one evolving picture, with provenance | Multi-agent reads (`read_agent_ids`, MA-2), `prime` (MA-3), `flush` (MA-1) |
| "The deck says $8M ARR (Jan); the filing says $5.2M (Mar)" | Bi-temporal validity + `supersede` + write-time conflict detection (§8/§12) |
| "A rumor and an audited filing are not equal evidence" | Source reliability + belief/corroboration (CC-*) |
| "Who owns whom / what depends on what / who's a related party" | Entity–relation graph, traversal, PageRank salience, communities (§9) |
| "Reconcile the disputes and distill a memo" | Dream State consolidation (§13) |
| "Name the relationships the corpus keeps implying" | Ontology evolution (§13) |

**The wow moment:** the **red-team agent** surfaces *"the deck's ARR (Jan) is superseded by
the audited filing (Mar) and contradicted by the churn data — belief 0.3, flagged"* — a
contradiction **no single specialist saw**, caught only because memory is *shared*,
*bi-temporal*, and *corroboration-weighted*. You cannot produce that on Vercel alone.

---

## The core loop

```
Analyst (human or auto) opens a Room on a target + its data room
  → the campaign DISPATCHES specialist agents (Financial, Legal, Technical, Market)
      each reads its slice, extracts CLAIMS, writes to shared memory
      under its own agent_id with { source, source_reliability, as_of }
  → a FLUSH barrier commits the round (MA-1)
  → the RED-TEAM agent reads across all specialists (read_agent_ids, MA-2),
      finds contradictions + stale claims → supersede / flag needs_review
  → a CONSOLIDATION pass (Dream State + salience + community) reconciles + ranks
  → the SYNTHESIS agent primes across the whole team (MA-3) and writes the
      INVESTMENT MEMO with an evidence chain (claim → sources → belief → verdict)
  → (continuous variant) a Cron pass re-investigates; new filings SUPERSEDE old claims
```

The **memory core is the source of truth**; Vercel Workflows/`eve` only schedule the
agents and call the same `/v1` verbs a human would. The runtime never owns memory.

---

## Architecture (the Vercel stack + the core)

```
Next.js on Vercel (the "war room")                 Python core (FastAPI, container)     ArangoDB
  • Vercel Workflows — durable campaign     ──/v1──▶  ingest · retrieve · lifecycle   ──▶ graph +
  • eve (optional) — specialists as agents             conflict/supersede · dream          vector +
  • AI SDK — agent loops + tools                       salience · community · ontology     BM25
  • Fluid Compute — long per-agent research loops
  • generative UI — live evidence graph, contradiction feed, memo
  • Cron — scheduled re-investigation (continuous variant)
```

- **Core stays a long-lived container** (Railway/Fly/VM) — durable write worker + in-process
  Faiss make it serverless-hostile (see the dungeon README / E-7 rationale). Unchanged.
- **Agents** start as **AI SDK** `generateText` + tools (fast, stable). `eve` is the
  *stretch* variant to show the app running natively on Vercel's agent stack (beta; pin it).

---

## Parallel development & isolation (do not break the dungeon)

The Diligence Room is built **alongside** `examples/dungeon`, which must keep working the
whole time. Isolation is enforced structurally, not by discipline:

1. **New directory only.** Everything lives under `examples/diligence-room/` — its own
   Next.js app, `package.json`, config, and `.env.local`. **DR-0 touches zero dungeon
   files.** No shared imports at first.
2. **Copy, don't extract (until deliberate).** Reusable pieces (typed core client,
   `GraphExplorer`, generative-UI card patterns) are **copied** into the new app initially.
   Extracting them into a shared `packages/*` is a *separate, later* refactor with its own
   PR that must keep the dungeon importing an unchanged API — never bundled into feature work.
3. **Separate port + separate Vercel project.** Dev server on **:3001** (dungeon keeps
   :3000); a distinct Vercel project for deploys. The dungeon's project is untouched.
4. **Shared core, isolated tenants.** Both apps hit the same Python core, but the Diligence
   Room writes only under its own tenant namespace (`room:<id>`) — **never `dungeon-player`**.
   Tenant isolation is already a core guarantee, so neither app can read or corrupt the
   other's data.
5. **Core changes are additive + back-compat, and gated.** The only likely core work (DR-4a,
   a *new* read endpoint for conflicts/provenance) must be purely additive — no change to any
   existing endpoint's behavior. Every PR runs the **Dungeon (typecheck, build)** and **Core
   (lint, type, test)** CI jobs, so a core change that breaks the dungeon fails CI. DR-0a adds
   a **Diligence (typecheck, build)** job so the new app is gated symmetrically.

**The rule:** if a Diligence PR changes anything under `examples/dungeon/`, `packages/`, or
alters an existing `/v1` endpoint's behavior, it's out of scope for that PR — split it out.

---

## Work packages

Same conventions as [GUILD.md](GUILD.md)/[ROADMAP.md](ROADMAP.md): each is a branch → PR →
squash-merge with keyless CI green. IDs are `DR-*`. Sizes: **S ≈ ≤1 day, M ≈ 2–3 days,
L ≈ ~1 week**. Estimates assume one engineer fluent in this codebase, reusing the
dungeon's building blocks (typed core client, GraphExplorer, generative-UI cards,
docker-compose, Fluid/Workflows learnings). "Keyless" isn't possible here — the agents need
a real generator; CI gates the *deterministic golden run* (DR-5a) with a recorded fixture.

| # | ID | Item | Size | Depends on |
|---|----|------|------|-----------|
| 0 | DR-0a | App scaffold: Next.js + typed core client + docker-compose + env | S | — |
| 1 | DR-0b | Data-room fixtures: a fictional target with planted contradictions/drift | M | — |
| 2 | DR-0c | Claim model + source-reliability priors (store convention + helpers) | S–M | DR-0a |
| 3 | DR-1a | Specialist #1 end-to-end (Financial): read slice → extract claims → store w/ provenance | M | DR-0c |
| 4 | DR-1b | Specialists #2–4 (Legal, Technical, Market) on the same pattern | M | DR-1a |
| 5 | DR-1c | **Red-Team agent** — cross-agent read, contradiction + stale detection, supersede/flag | M–L | DR-1b |
| 6 | DR-1d | Synthesis agent — prime across team → investment memo + evidence chain | M | DR-1c |
| 7 | DR-2a | Durable campaign (Vercel Workflows): dispatch → flush → red-team → consolidate → memo, resumable | M–L | DR-1d |
| 8 | DR-2b | Consolidation wiring: Dream State + salience + community + ontology between phases | S–M | DR-2a |
| 9 | DR-3a | War-room UI: live evidence graph (reuse GraphExplorer) + entity inspect | M | DR-1b |
| 10 | DR-3b | Contradiction feed + belief meters + agent-activity timeline (generative UI) | M | DR-1c |
| 11 | DR-3c | Memo view: assembled memo w/ inline citations + evidence chain, exportable | M | DR-1d |
| 12 | DR-3d | Room controls + spectator mode + campaign status | S | DR-2a |
| 13 | DR-4a | Core: list-conflicts/flagged/superseded read endpoint (if not already exposed) | S–M | — |
| 14 | DR-4b | Core: source-reliability priors + claim provenance surfaced in reads | S | DR-4a |
| 15 | DR-5a | **Deterministic golden run** — a seeded campaign that reliably surfaces the planted contradictions (demo reliability + CI gate) | M | DR-2a |
| 16 | DR-5b | Docs (this file → run guide) + deploy config (Fluid + core container) | S | all |
| 17 | DR-5c | *(stretch)* `eve` variant — recast specialists as agent directories | M–L | DR-1b |
| 18 | DR-5d | *(stretch)* Real connectors (web search / EDGAR) + continuous Cron re-investigation | L | DR-2a |
| 19 | DR-7 | Live-run latency: split the red-team and synthesis steps into retrieval vs LLM time, then cut the larger share (scoped below) | M | DR-2a |

**Recommended sequence:** DR-0 → one specialist (DR-1a) → the red-team + synthesis (DR-1c/d)
on a *sequential* runner first (prove the thesis fast), then wrap in Workflows (DR-2), then
the war-room UI (DR-3), then golden run + docs (DR-5). Core additions (DR-4) slot in only if
the existing endpoints don't already surface conflicts/provenance. Stretch items last.

## Status (shipped)

The demo is feature-complete. Run guide: [`examples/diligence-room/README.md`](../examples/diligence-room/README.md).

- **DR-0 … DR-2** ✅ scaffold, data-room fixtures + claim model, the four specialists, the
  red-team, the synthesis agent, and a durable single-call campaign.
- **DR-3 (war-room UI)** ✅ shipped as **DR-3a…DR-3g** (rescoped during detailed DR-3 design
  into finer packages): evidence-graph hero, live agent rail + pipeline, contradiction feed with
  graph cross-highlight, investment-memo slide-over with evidence chains + Markdown export,
  guided-narration ribbon, and "why shared memory" callouts. Live **and** canned (auto-fallback
  to the golden replay when no provider key), so the stage demo can't break.
- **DR-4 (core conflicts/provenance)** ✅ *satisfied by existing endpoints* — reliability and
  provenance are already threaded through `store` and surfaced via `retrieve`/`prime`, so no core
  change was needed (keeping dungeon isolation at zero files).
- **DR-5a** ✅ deterministic golden run + keyless CI gate (`lib/golden-oracle.ts`,
  `test/golden-run.test.ts`) that locks the fixture to the planted-defect oracle.
- **DR-5b** ✅ this run guide + Fluid Compute deploy config (`vercel.json`).
- **DR-5c / DR-5d** — *(stretch, not started)* `eve` variant; real connectors + Cron.

Isolation held throughout: **0 files changed under `examples/dungeon`** across every DR PR.

---

### DR-7 — Live-run latency: the retrieval and LLM stages (scope)

**Where a live run stands (2026-11-20, hosted core, after #284–#292).** One run takes about 58 s
from the first specialist to the memo, down from over 5 minutes:

| step | time | what is in it |
|---|---|---|
| specialists (4 in parallel) | 3.8 s | 23 docs, 5 extractions in flight per specialist |
| flush:specialists | 14.8 s | the core drains ~104 queued claims (write path, see ROADMAP WQ-1) |
| consolidate | 0.6 s | salience + communities |
| red-team | 12.6 s | `gatherClaims` (core retrieve, k=24) **plus** one `generateObject` call |
| flush:redteam | 4.2 s | |
| synthesis | 20.0 s | `prime` (core retrieve, k=40) **plus** one `generateObject` call |

The red-team and synthesis steps are about 33 s, over half the run, and each is two things in a
row: a core read, then an LLM call. **We do not yet know the split.** The core's `/health`
reported `retrieval.lite` with only two samples (p50 4.9 s, p95 9.7 s) for that run. The two are
probably the red-team's gather and the synthesis `prime`, which would put about 15 s of the 33 s
in core retrieval and only about 18 s in LLM generation, but that is an inference from two
samples, not a measurement. An idle core answered `retrieval.lite` at about 1.2 s earlier, so
retrieval may be slower here because it runs right after the write burst. Do not pick a fix
before the split is measured.

**Step 0 — measure (S, one small PR).** In the workflow steps, log the stages separately:
- red-team: `gather` ms (core retrieve), LLM ms, input and output tokens, finish reason,
  number of disputes, and the record-writes ms;
- synthesis: `prime` ms, LLM ms, input and output tokens, finish reason, findings count;
- core side: `retrieve`/`prime` already feed `/health` `retrieval.*`; add per-stage timers to the
  retrieve path the way `write.*` has them (#289) if the core read is the larger share.
Run it three times on a reset room and take medians. Output tokens divided by LLM ms gives the
generation speed; if that is roughly constant, output length is what costs time.

**Candidate levers, by which share Step 0 says is large**

*If the LLM calls dominate (output-bound):*
1. **Index-based evidence in the memo.** The memo's `evidence` arrays are the longest part of the
   output (each finding repeats claim text). Number the claims in the briefing, have the model
   output indices (`[3]`, `[7]`), and expand them to the claim text on our side. Far fewer output
   tokens, and the evidence chains stop being able to quote a claim that is not in memory, which
   is a correctness gain for the demo's central promise. Quality risk: low, but check the memo
   still reads well with the expanded text.
2. **Parallel red-team by dispute kind.** One call per kind (`contradiction`, `temporal_drift`,
   `reliability`, `related_party`, `stale`), each over the **full** claim pool and reporting only
   its kind, run together; then merge and de-duplicate. Wall time is the slowest call, not the sum,
   and each sees every specialist's claims, so cross-specialist visibility, the point of the
   red-team, is kept. Costs about 5x input tokens (input is the cheap, fast side) and needs a
   de-duplication pass for a defect reported under two kinds.
3. **Tighter schemas.** One-sentence summaries, a cap on findings, `maxOutputTokens` set from the
   measured output. Cheapest, but cuts content, so it needs the coverage check below.

*If core retrieval dominates:*
4. **Why is `retrieve` 5–10 s here?** Measure with timers first. Suspects: contention with the
   write drain just before it, the cost of the reranker or graph arm at k=40, and view-sync lag
   after a burst of writes. Fixes depend on the answer (for example, lower k for the briefing, or
   skip the reranker for `prime`).
5. **Overlap the reads.** `prime` for synthesis needs the red-team's findings, but the specialists'
   half of the briefing does not, so it could be fetched while the red-team runs. Changes the
   "synthesis inherits the red-team's reasoning" story, so only if the read share is large.

*Either way (perceived speed, not total time):*
6. **Stream the structured output** (`streamObject`) so disputes and memo sections appear as they
   are generated. Does not shorten the run; makes it feel shorter. Lowest priority now that the
   memo no longer opens by itself and the viewer reads the contradictions while synthesis runs.

Not worth doing: a different model (Haiku 5.5 is already the fast one, and extended thinking is
already off for these calls) and anything on the write path (WQ-1 is done).

**Guarding quality.** Any lever changes model input or output, so compare before and after on the
same reset room, at least 5 runs each: (a) planted-defect coverage using the existing
`goldenCoverage(DEFECTS, disputes)` from `lib/golden-oracle.ts` on the live disputes (it matches
kind and subject, so a dropped dispute type shows up immediately); (b) the memo recommendation and
finding count; (c) wall time per step from the new logs. A lever that drops coverage below the
current baseline is rejected whatever it saves.

**Acceptance.** The median live run is at most about 40 s (from 58 s), with planted-defect
coverage no lower than the baseline over 5 runs, and the per-stage log lines in place so the next
regression is visible. If Step 0 shows the split is close to even, expect a smaller win and say so.

**Rough size.** Step 0: S. Lever 1 (index-based evidence): S–M. Lever 2 (parallel red-team): M.
Lever 4 (core retrieval): S–M, depends on the finding.

### DR-0a — Scaffold (the first step, detailed)

The goal is a **thin, provably-connected, independently-CI'd** app that touches nothing in
the dungeon. Deliverables:
- `examples/diligence-room/` — new Next.js App-Router app, TS `strict`, mirroring the
  dungeon's tooling (`tsconfig`, eslint, `next.config.ts`, `package.json`). Its own
  `.env.example` + `.env.local` (`CORE_URL`, `CORE_API_KEY`, `AI_GATEWAY_API_KEY` /
  `ANTHROPIC_API_KEY`).
- `lib/core.ts` — a typed server-side core client **copied and adapted** from the dungeon's,
  scoped to a **`room:<id>` tenant** (never `dungeon-player`). DR-0a includes only what the
  scaffold needs: `health()`, and stubs/signatures for `storeClaim`, `retrieve`,
  `prime`, `flush`, `graph` (fleshed out in DR-0c/DR-1).
- `app/api/health/route.ts` — proxies core `/health`; `app/page.tsx` — a minimal
  landing ("Open a Room") that shows **core online/offline** (proves app ↔ core wiring).
- `.claude/launch.json` — add a `diligence` config on **port 3001** (dungeon's `dungeon`
  config on 3000 is untouched). Reuse the existing root `docker-compose` for core + db.
- **CI:** add a `Diligence (typecheck, build)` job to `.github/workflows/*` mirroring the
  Dungeon job. The existing Dungeon + Core jobs stay — together they are the guarantee that
  this work can't silently break the dungeon.
- A couple of unit tests for `lib/core.ts` transforms (Vitest), as the dungeon has.

**Acceptance (DR-0a).** `npm run typecheck && npm run build` green for the new app; the
`Diligence` CI job passes; dev server runs on :3001 and `/api/health` reflects real core
status; **the dungeon's CI job is still green and no file under `examples/dungeon/` changed**
(enforceable with `git diff --name-only origin/main -- examples/dungeon | wc -l == 0`).

### DR-0b — Data-room fixtures (the content that makes or breaks the demo)

A fictional target — e.g. **"Northwind Robotics"** — with ~15–25 curated source documents:
pitch deck, two quarterly filings *across time*, an org chart, a cap table, 3–4 news
articles, a customer contract, a churn export. Seed **5–8 deliberate defects** that only a
shared, temporal, corroboration-aware memory can catch:
- **Temporal drift:** deck ARR $8M (Jan) vs audited filing $5.2M (Mar) → supersession.
- **Direct contradiction:** management "no material litigation" vs a court-records article.
- **Unequal sources:** a blog rumor vs a signed contract on the same customer.
- **Hidden relationship:** a "customer" that a cap-table entity secretly owns (related-party
  → community detection).
- **Stale-but-uncontradicted:** a claim no later source refutes (belief stays moderate).

This is deceptively time-consuming to make *convincing* — budget real design time. It also
doubles as the golden-run fixture (DR-5a).

### DR-1c — The Red-Team agent (the star, and the riskiest part)

Reads every specialist's claims via `read_agent_ids: [financial, legal, technical, market,
diligence::shared]`, then for each subject: detects (a) **conflicts** (same subject,
incompatible values), (b) **stale** claims (an earlier `as_of` superseded by a later one),
(c) **uncorroborated** high-impact claims (belief below threshold). Records disputes via
`supersede` / `needs_review`, and *writes its own reasoning* (MA-4 capture) so the synthesis
agent inherits the analysis, not just the flags. The engineering risk is making the memory's
conflict signals reliably surface the *planted* contradictions — this is where most of the
build's uncertainty lives.

### DR-3 — War-room UI (spectator view)

Reuse the dungeon's `GraphExplorer` for the live evidence graph; add a **contradiction feed**
(each dispute with its two sources + belief), **belief meters** per key claim, an
**agent-activity timeline** (who found what, when), and the **memo** assembling with inline
citations. The human *watches the team think* — the business version of the dungeon's
spectator mode.

---

## What's new vs reused

- **Reused (accelerators):** the memory core itself (≈90% unchanged), the typed core client
  pattern (`lib/core.ts`), `GraphExplorer`, generative-UI card patterns, docker-compose,
  Fluid Compute + Workflows learnings, `prime`/`flush`/`read_agent_ids`/`supersede`.
- **New:** the app + orchestration, four specialist agents + red-team + synthesis, the
  data-room fixtures, the war-room UI, a claim convention, and (maybe) one small core read
  endpoint for conflicts/provenance.

## Acceptance

A Room opened on the fixture target, with **no human steering**, produces an investment memo
that (1) **catches ≥4 of the planted defects** (the ARR supersession, the litigation
contradiction, the related-party link, an uncorroborated claim), (2) **cites an evidence
chain** (claim → sources → belief → verdict) for each finding, and (3) is **resumable** —
killing the deployment mid-campaign and redeploying continues the same Room. The war-room UI
shows the evidence graph, contradiction feed, and belief meters updating as agents act.
**Determinism:** the golden run (DR-5a) reproduces the same defects each time for a reliable
live demo. **The disqualifier:** if you can strip out arango-agentic-memory and still pass
acceptance, the demo has failed its purpose — every finding must trace to shared, temporal,
corroborated memory.

## Risks

- **Content design (DR-0b)** — convincing planted defects take longer than they look.
- **Red-team reliability (DR-1c)** — surfacing the *right* contradictions from LLM agents is
  stochastic; the golden run (DR-5a) + tuned reliability priors are the mitigation.
- **Demo determinism** — LLM agents drift; ship a seeded/recorded golden path for the stage.
- **eve is beta** — keep it a stretch variant; build on AI SDK first.
- **Cost/runaway** — cap agents, turns, and re-investigation frequency; every turn is metered.
