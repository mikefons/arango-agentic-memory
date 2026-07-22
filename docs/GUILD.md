# The Guild — Memory Dungeon 2.0 (design + roadmap)

**Goal:** evolve `examples/dungeon` from a single-DM chat game into the **showcase for
multi-agent handoff** ([ROADMAP.md](ROADMAP.md) MA-1..MA-4) — and into a genuinely fun
game. Companion to the MA roadmap: the MA items build the coordination layer; this doc
builds the thing that makes people *feel* it.

**The problem with the current game.** One agent (`dm`), one tenant, one session.
Memory makes the DM slightly more consistent — but nothing in the game would break
without the memory core, so nothing in it demonstrates the memory core. And as a game
it has no stakes, no fail state, no reason to return.

**The reframe.** The player is the **Guildmaster**. The dungeon (Ashfall Keep) is
deliberately too big to solve in one run. You send in **expendable heroes, one
expedition at a time** — each hero is a *fresh agent with an empty context window*.
The only thing that survives an expedition is what was written to the guild's shared
memory.

> **The product thesis as a game rule: context dies with the agent; memory outlives it.**
> The player doesn't read about handoff — they feel it every time a torch burns out.

---

## The core loop

1. **Expedition.** The Guildmaster briefs a hero and sends them in. The hero has a
   **torch** — a visible turn budget (the context window as a game resource). They
   explore, interrogate NPCs, form suspicions, write findings.
2. **The torch dies.** Expedition over; the hero retires (or *perishes* if they
   confronted the wrong NPC). Their context window is gone forever. A **"Chronicling…"**
   interstitial runs — MA-1's `flush` barrier played straight: memories committing to
   the guild ledger, on screen.
3. **The Handoff** — the star UI moment. Before the next hero enters, the game shows
   the **briefing being assembled**: relevant history, key entities, prior tool runs,
   filling a token-budget bar. This renders MA-3's `/v1/prime` response literally.
   The player can **pin or drop** items against the budget — `max_memory_tokens` as a
   strategic choice, not a config knob.
4. **Next hero.** A different persona (rotating archetypes) walks in already knowing
   what the guild knows. NPCs react: *"Another one of you guild people? I already told
   the bard where I was that night."*
5. **Endgame.** A dungeon-wide mystery — a **traitor NPC** whose claims systematically
   contradict across expeditions. You win by **accusing with an evidence chain** drawn
   from the graph (supersedes, corroboration, belief as gameplay). Multiple expeditions
   are required *by design*.

### Why it's a better game

- **Stakes and loss:** torches, hero death, and a **"flee without chronicling"** panic
  option that demonstrably loses the final turns' memories — MA-1's failure mode taught
  through pain.
- **Comedy engine:** distinct hero voices reacting to shared memory (the literal-minded
  golem reading the bard's flowery ledger entries) beats one bland DM.
- **Compounding progress:** the map fills in across runs, the dossier thickens, the
  ledger levels up — roguelike meta-progression, which is exactly what an external
  memory system *is*.
- **A real mystery:** the existing lie engine becomes the win condition instead of a
  side mechanic.

### Roadmap features, in-game

| Roadmap item | In-game as |
|---|---|
| MA-1 sync/flush | The "Chronicling…" barrier between expeditions; "flee without chronicling" loses unflushed memories |
| MA-2 `read_agent_ids` | Each hero = own `agent_id`; retrieval spans `[hero, guild::query, guild::insight]`; graph nodes colored by *which hero* learned it |
| MA-3 `prime` | The Handoff Briefing screen, rendered from the endpoint response |
| MA-4 output capture | Heroes' *conclusions* ("I suspect the cook") persist — the next hero inherits reasoning, not just facts |
| MA-7 insight protection | "Only the Chronicler may write in the Great Ledger" — Dream State re-skinned as the character who pens insights between runs |
| MA-5 handoff eval | The game is the manual, playable version of the eval scenario |

### What survives from the current game

Nearly everything: `lib/world.ts` (rooms, NPCs, claims, evidence), the lie engine +
`confront`/`supersede` flow, the dossier, graph explorer, dream state, scene art, flags,
share cards, and the deploy pipeline. This is a **reframe plus one new screen**, not a
rewrite.

---

## Work packages

Same conventions as [ROADMAP.md](ROADMAP.md): each is a self-contained branch → PR →
squash-merge with keyless CI green. IDs are `E-*` (expedition). Sizes: S ≈ ≤1 day,
M ≈ 2–3 days.

| # | ID | Item | Size | Depends on |
|---|----|------|------|-----------|
| 1 | E-1 | Expedition lifecycle: heroes, torch, chronicle | M | — (works on today's core) |
| 2 | E-2 | Handoff Briefing screen | M | MA-1, MA-2, MA-3 |
| 3 | E-3 | Hero personas + guild-aware NPCs | S | E-1 |
| 4 | E-4 | Traitor arc + accusation endgame | M | E-1 |
| 5 | E-5 | Meta-progression + onboarding + polish | S | E-1..E-4 |
| 6 | E-6 | Autonomous multi-agent expeditions (durable-agent runtime) | L | E-1..E-4 |
| 7 | E-7 | Vercel OIDC auth for the dungeon→core hop | S | JWT-1/2/3 (core, landed) |

Recommended sequence: **E-1 now** (no core dependencies), then **E-3/E-4** in either
order, with **E-2** slotting in whenever MA-1..3 land. E-5 last. **E-6 is a post-1.0
showcase track** — build it only once E-1..E-5 land and the human-driven loop is solid.
**E-7 is independent** (integration hardening) — land it anytime the deployed demo needs
to drop the shared static key.

---

### E-1 — Expedition lifecycle: heroes, torch, chronicle

**Problem.** The game has one immortal agent (`AGENT = "dm"`, `SESSION = "run-1"`
hard-coded in `app/api/chat/route.ts`) and no run boundaries. Nothing establishes
"agents are ephemeral, memory persists."

**Design.**
- **Expedition state.** Extend `GameState` (`lib/tools.ts`) with
  `{ expedition: number, heroId: string, torch: number }`. New module
  `lib/expedition.ts` (pure, unit-testable): torch budget (default ~12 turns),
  `nextHero(expedition)` → `hero-<n>`, expedition end conditions
  (torch exhausted | perished | fled).
- **Per-hero agent identity.** The chat route + middleware take the hero's id from the
  request (`agentId: heroId`, `sessionId: expedition-<n>`), replacing the constants.
  The hero's turn writes go under its own `agent_id` (episodic + procedural), exactly
  as today.
- **Shared guild tier — today's core, no MA-2 needed yet.** Facts meant to outlive the
  hero are *explicitly* stored to the shared namespace: the `talk`/`confront`/`take`
  tools' existing `remember(...)` calls switch ctx to
  `agent_id: "guild::query"` (mirrors the CrewAI tier convention, §14). The hero's
  middleware retrieval can't span namespaces until MA-2, so **the chat middleware's
  tenant ctx uses `guild::query` for retrieval** in the interim — one shared brain,
  per-hero session ids. (E-2 upgrades this to true per-hero + cross-tier reads.)
- **Torch UI.** A burn-down indicator in `DungeonGame.tsx` (torch icon + turns left);
  each completed turn decrements. At 0 → expedition-end modal.
- **Chronicle step.** Expedition end triggers, in order: a final `remember()` of the
  hero's summary ("Expedition 3, Brann the Bold: reached the crypt, the cook's alibi
  contradicts the guard's account"), then the existing dream endpoint
  (`/api/dream`) re-labeled **"The Chronicler writes…"** with its report toast, then
  hero increment + torch reset. "Flee" (available any time) skips the summary + dream —
  the fast, lossy exit (becomes *demonstrably* lossy once MA-1's flush exists to
  contrast against).
- **Persistence.** Expedition counter + map-seen state live in the existing
  localStorage game save.

**Files.** `lib/expedition.ts` (new), `lib/tools.ts` (ctx threading + GameState),
`app/api/chat/route.ts` (dynamic agent/session), `components/DungeonGame.tsx` (torch,
end-of-run modal, chronicle flow), `lib/prompt.ts` (DM narrates expeditions),
`test/expedition.test.ts` (new), README.

**Tests.** Torch decrements and triggers end at 0; hero id sequence; expedition end
reasons; tools write to `guild::query` ctx (assert fetch bodies with a mocked
`core.store`); state round-trips localStorage.

**Acceptance.** Play expedition 1, learn a claim, end it; expedition 2's hero retrieves
that claim on turn 1 (shared-tier read). The DM's narration acknowledges the new hero.

**Out of scope.** The briefing screen (E-2), personas (E-3) — expedition 2's hero can
be "hero-2" with the same voice for now.

**Shipped** (`lib/expedition.ts`, `app/api/chronicle/route.ts`, chat-route rewiring,
`DungeonGame` torch UI + end-of-expedition modal + flee, `test/expedition.test.ts`).
**Refinement vs. the original plan:** MA-2 is now shipped, so E-1 uses the *real*
`readAgentIds: [heroId, guild::query]` (the hero reads its own memory + the guild ledger
in one fused pass) instead of the interim "retrieve as guild::query" hack — the demo now
exercises MA-2 directly. World-fact tools write to `guild::query`; the chronicle summary
is a `sync` write (MA-1) so the next hero sees it on turn 1.

---

### E-2 — Handoff Briefing screen *(gated on MA-1 + MA-2 + MA-3)*

**Problem.** The handoff is the product's money shot and currently invisible: memory
becomes context inside the middleware where nobody sees it.

**Design.**
- **Chronicle barrier (MA-1).** The chronicle step calls `/v1/flush` (via a new
  `flush()` in `lib/core.ts`) and shows real states: "committing memories… flushed ✓".
  "Flee" skips it; the next briefing then *visibly lacks* the final turns — the loss is
  the lesson.
- **True per-hero memory (MA-2).** Retrieval ctx becomes the hero's own id +
  `read_agent_ids: [heroId, "guild::query", "guild::insight"]` (chat middleware gains
  the `readAgentIds` option). Tool writes revert to the hero's own id — sharing now
  happens by *reading across*, not writing to a communal account. Graph explorer +
  dossier surface `MemoryHit.agent_id` provenance: nodes/rows tinted per hero, tooltip
  "learned by Brann, expedition 3".
- **Briefing screen (MA-3).** New route `app/api/prime/route.ts` → core `prime(task,
  ctx)` with the current objective as the task. New `components/HandoffBriefing.tsx`
  between expeditions: the three briefing sections (history / entities / prior tool
  runs) rendered as ledger entries, a token-budget bar filling as items load, and
  **pin/drop** toggles per item — pinned items are kept, dropped ones freed, re-calling
  prime with the adjusted budget. "Send in the next hero" injects the final briefing as
  the new hero's opening system context (replacing the middleware's cold retrieve for
  turn 1).

**Files.** `lib/core.ts` (+`flush`, `prime`), `app/api/prime/route.ts` (new),
`components/HandoffBriefing.tsx` (new), `components/DungeonGame.tsx` (interstitial
flow), `components/GraphExplorer.tsx`/`Dossier.tsx` (provenance tint),
`app/api/chat/route.ts` (readAgentIds), tests for the prime route + briefing reducer
logic.

**Tests.** Briefing renders all sections from a canned prime response; pin/drop
recomputes budget; flee-path briefing lacks post-flee facts (integration, against
compose stack); provenance color mapping stable per hero.

**Acceptance.** A player can watch memory become context, shape it under a budget, and
see whose knowledge they're inheriting — with zero explanation needed.

**Shipped** (`app/api/prime` + `app/api/flush` routes, `lib/core` `prime()`/`flush()`,
`lib/briefing.ts` + test, `components/HandoffBriefing.tsx`, `DungeonGame` phase flow,
briefing CSS). **Refinements vs. the plan:** (1) **provenance is briefing badges**
(`you`/`guild` on history items via `MemoryHit.agent_id`), *not* graph-node tint — E-1
writes all world facts to `guild::query`, so per-hero graph tint would be meaningless;
(2) **injection is visualize-only** — the screen previews `prime()`, and the hero's real
turn-1 context still comes from the middleware `retrieve(readAgentIds)` (which reads the
same ledger); pin/drop is a budget-visualization affordance. Literal first-turn injection
is a future enhancement.

---

### E-3 — Hero personas + guild-aware NPCs

**Problem.** One narrator voice makes every expedition feel identical; NPCs don't
acknowledge that different agents share one memory — the multi-agent premise is
invisible in the fiction.

**Design.**
- **Personas.** `lib/personas.ts`: ~6 archetypes (cowardly bard, literal-minded golem,
  arrogant knight, superstitious gravedigger, over-caffeinated alchemist, retired
  assassin), each = name generator + a voice block appended to `DM_SYSTEM` ("narrate
  this hero's actions in their voice; the hero's asides reflect their temperament") +
  a portrait glyph for the UI. `nextHero()` (E-1) picks the next persona round-robin
  with names varying per run.
- **Guild-aware NPCs.** Extend `lib/prompt.ts` so the DM knows: heroes share the guild
  ledger, NPCs remember prior guild visitors (their memory *is* the retrieved context —
  when a hit's provenance is a previous hero, the DM voices NPC reactions to being
  re-questioned: "I already told the bard…"). Cheap, high-comedy, and it *explains
  provenance diegetically*.
- **Persona flavor in memory.** Hero summaries (E-1 chronicle) are written in-voice —
  future heroes literally read the bard's purple prose in their briefing (funnier once
  MA-4 captures assistant outputs).

**Files.** `lib/personas.ts` (new), `lib/expedition.ts` (persona rotation),
`lib/prompt.ts`, `components/DungeonGame.tsx` (hero nameplate/portrait),
`test/personas.test.ts`.

**Tests.** Rotation is deterministic given expedition number; system prompt contains
exactly one persona block; nameplate renders.

**Acceptance.** Two consecutive expeditions read as *different characters* inheriting
one memory, and at least one NPC line references a previous hero's visit.

**Shipped** (`lib/personas.ts` + test, chat-route voice append, guild-aware NPC prompt
line, persona name/glyph across the pill / intro / end-modal / briefing / in-voice
chronicle). **Decision honored:** the memory `agent_id` stays `hero-N`; the persona is a
pure display + prompt layer (6 archetypes round-robin, names rotate per lap, deterministic).

---

### E-4 — Traitor arc + accusation endgame

**Problem.** No win condition. The lie engine (claims, `confront`, supersedes,
dossier trust) exists but resolves per-claim; nothing spans the whole dungeon or ends
the game.

**Design.**
- **The traitor.** Designate one NPC in `lib/world.ts` as the arc's culprit; author a
  claim web where the traitor's claims contradict evidence *distributed across rooms
  and NPCs* such that no single torch's worth of turns can gather it all — the "too big
  for one context window" guarantee, enforced by content design (≥ 2×torch claims
  needed on the critical path). Reuse the existing `Claim`/`Evidence`/`ClaimNeed`
  machinery — this is authoring plus a threshold, not a new engine.
- **Accusation.** New `accuse` tool (`lib/tools.ts`): the hero formally accuses an NPC.
  Resolution is deterministic from the dossier: count the accused's claims that are
  `caught` (superseded with evidence). ≥ N (tunable, e.g. 3) → **win**: the confession
  scene + an "evidence chain" panel rendering the supersede edges from the graph.
  Wrong NPC or insufficient evidence → the *hero perishes* (expedition ends
  immediately, un-chronicled — stakes) and the dungeon's NPCs grow warier (a flag the
  DM prompt uses to make future interrogations cagier).
- **Perish rule generally.** `confront` with wrong/no evidence (already detectable in
  the lie engine result) now costs extra torch turns; a failed `accuse` is fatal. Death
  screen + "the guild recruits another" beat.
- **Win screen.** Case-closed report: expeditions used, heroes lost, the evidence
  chain, tokens of memory accrued — the stats double as a memory-system brag sheet.
  Reuses the share-card path (`lib/share.ts`, `/api/og`).

**Files.** `lib/world.ts` (traitor claim web), `lib/tools.ts` (`accuse`, perish
outcomes), `lib/lie-engine.ts` (evidence-count helper), `components/DungeonGame.tsx`
(death/win screens, evidence chain panel), `lib/prompt.ts` (wary mode),
`test/accuse.test.ts` + extended `world.test.ts` (assert the critical path exceeds one
torch).

**Tests.** Accusation math (win at threshold, fatal below); traitor's claim web is
internally consistent (every gold contradiction has a reachable evidence source);
critical-path claim count > torch budget; perish ends expedition without chronicle.

**Acceptance.** The game is winnable in ~3–5 expeditions by a player who uses the
dossier, and unwinnable in one — verified by the content test, not vibes.

**Shipped** (`lib/accuse.ts` + test, traitor **Saro** + Counting House in `lib/world.ts`,
`accuse` tool, win/perish overlays + evidence chain, `wary` flag + prompt line).
**The load-bearing decision:** the accusation counts caught lies from the **persistent
`/v1/graph`** (superseded lie-subjects), *not* per-hero `caughtClaims` — so catches
accumulate across expeditions (hero-1 catches two, hero-3 catches the last, accuses →
win). **Threshold refined to 4** (GUILD said "e.g. 3, tunable") so the structural
critical-path lower bound is 14 > the 12-turn torch — unwinnable in one, proven by the
test. A wrong/unproven accusation is fatal (perish, un-chronicled) and leaves NPCs warier.

---

### E-5 — Meta-progression + onboarding + polish

**Problem.** Nothing communicates compounding progress between runs, and the current
game drops players in cold.

**Scope.**
- **Guild ledger panel:** expedition count, heroes lost, rooms discovered (map fill
  %), claims heard/caught, mystery progress (evidence toward threshold, shown as a
  case-board meter). Data: existing `stats`/dossier + localStorage.
- **Map persistence across expeditions:** `DungeonMap` renders all rooms *ever* seen
  (guild knowledge) vs the current hero's visited set (context) in two tones — the
  memory-vs-context distinction as cartography.
- **Onboarding:** a 3-beat intro (Guildmaster letter → torch explained → first
  objective) on first load; a one-line hint system pointing at the dossier the first
  time a contradiction is caught.
- **Expedition report share card:** extend the OG card with hero name/persona +
  expedition stats.
- **Cleanup:** rename UI strings from "Memory Dungeon" DM framing to Guild framing;
  README rewrite with the loop + a GIF.

**Files.** `components/` (LedgerPanel new, DungeonMap two-tone, intro modal),
`lib/share.ts`, `app/api/og/route.ts`, README.

**Acceptance.** A first-time player understands the loop without reading the README;
a returning player can see at a glance what the guild knows that their current hero
hasn't loaded.

**Shipped** (`lib/guild.ts` — pure meta-progression + two-tone cartography helpers;
`components/LedgerPanel.tsx` — Guild Ledger stats + case-board meter reading the
persistent `/v1/graph`; `components/GuildIntro.tsx` — 3-beat onboarding; `DungeonMap`
two-tone rooms (guild memory vs hero context) via `roomTint`; first-catch hint +
`GameState.visited` tracking in `DungeonGame`; `share.ts`/`api/og` hero-persona card;
"Memory Dungeon" → "The Guild" reframing; README loop + GIF placeholder). **Refinements
vs. the plan:** (1) `expeditions` is derived from the live hero number, not double-stored
— `GuildSave` persists only `heroesLost` + the room/claim unions, avoiding drift;
(2) the case meter reuses `caughtCount` on the traitor so it reflects *exactly* when
`accuse` will succeed; (3) the demo GIF is a committed placeholder (`docs/guild-loop.gif`)
to be re-recorded — CI's offline link-check needs the target to resolve.

---

### E-6 — Autonomous multi-agent expeditions (durable-agent runtime) *(post-1.0 showcase)*

**Problem.** E-1..E-5 make the handoff *legible* by keeping a human in the loop — the
Guildmaster clicks each turn, chronicles, and shapes the briefing. That's the right call
for teaching the idea, but it means the multi-agent story is still *narrated by a person*.
The strongest version of the thesis is an agent handing off to another agent **with no
human between them**: expeditions that run themselves, so the only thing connecting Hero N
to Hero N+1 is the guild's memory. This deliberately revisits the "one hero at a time"
scope call below — as a separate, opt-in showcase, not a change to the core demo.

**Why a durable-agent runtime earns its place here (and nowhere else).** The human-driven
loop does *not* need one — the memory core's durable queue + `flush` barrier are already the
coordination substrate, and adding a second durable layer would only blur that. Autonomy is
the one case that flips: when nobody is clicking, *something* server-side must durably drive
`descend → explore(N turns) → chronicle → prime → descend` across minutes/hours, survive
restarts, retry a failed hero, and fan out to bounded-parallel expeditions. That is exactly
durable-orchestration territory.

**The one hard rule, whichever runtime.** The memory core stays the source of truth; the
runtime is only the *scheduler + agent host* — it decides when to spawn the next hero and
calls the same `/v1` verbs a human would. It must **not** own memory. This matters most for
`eve` (below), which ships its own agent-state/persistence: for the guild ledger you
deliberately bypass it and write to the core, or the whole "context dies, memory outlives it"
thesis dissolves into the framework's own store.

**Runtime candidates (evaluate at build time — don't default).**
- **Vercel Workflows** — durable step functions. Lowest-level, most explicit: you author
  `spawnHero → runExpedition → chronicle → prime → spawnHero` as durable steps, and nothing
  competes with the memory core for "what is the agent's state." Best when you want the
  orchestration visible and the dependency surface minimal.
- **`eve` (Vercel's durable-agent framework, [github.com/vercel/eve](https://github.com/vercel/eve))** —
  higher-level and arguably the more natural fit: a "hero" becomes an eve agent directory
  (`instructions.md` = the persona/DM prompt, `tools/` = thin wrappers over the `/v1` verbs +
  the game tools, `skills/` = interrogate/confront routines, `schedules/` = kick a campaign),
  and eve's runtime handles the durable loop. Trade-off: it's **beta** (APIs may change) and
  it brings its own agent-state opinions — acceptable on an experimental showcase branch,
  with the memory-ownership rule above enforced, but not something to bet the canonical demo
  on. Strongest pick if E-6 is also meant to show the app running *natively on Vercel's agent
  stack*.

Either way the `tools/` (or Workflow steps) call `store`/`flush`/`prime`/`retrieve`; the core
is the shared mind, the runtime is just the heroes' bodies.

**Design.** (Shown with Vercel Workflows; the `eve` variant maps the same steps onto an
agent directory + its runtime — see candidates above.)
- **`workflows/expedition.ts` (durable run per campaign).** Steps:
  `spawnHero(n)` → `runExpedition(heroId)` (loops the agent's own `generateText` +
  tools for its torch budget, writing under `agent_id: hero-<n>` + `guild::query`) →
  `chronicle(heroId)` (`POST /v1/flush`, MA-1) → `brief(nextHeroId)` (`POST /v1/prime`,
  MA-3, auto-kept — no human pin/drop) → `spawnHero(n+1)`. Each step is a durable
  checkpoint: a crash resumes mid-campaign without re-running committed work. (Under `eve`,
  each step is an agent tool/skill; the durable loop is the runtime's.)
- **Autonomous hero = a real agent loop, not scripted.** The hero runs the same tool set
  as the human game (`look/move/talk/confront/accuse`) under `maxSteps`; its *reasoning*
  is captured (MA-4) so the next hero inherits conclusions, not just facts. Win condition
  unchanged: the campaign ends when the guild has exposed the traitor across expeditions.
- **Bounded parallelism (optional).** Fan out K heroes into different wings of the keep
  concurrently, then a `converge()` step primes a "chronicler" hero from all K agents'
  writes (`read_agent_ids: [hero-a, hero-b, …, guild::query]`, MA-2) — the multi-writer
  → single-reader fusion made visible. Keep K small (2–3) so the graph stays legible.
- **UI: a spectator mode.** The existing screens become read-only live views — the map,
  dossier, and case-board update as autonomous heroes act; the Handoff Briefing renders
  each auto-prime as it happens. The human watches memory compound instead of driving it.
- **Fluid Compute is the substrate.** The per-hero agent loop is a long-running,
  post-response, instance-reused function — precisely Fluid's sweet spot (already enabled
  in `vercel.json`). Workflows orchestrates; Fluid runs the steps.

**Roadmap features, in-game (extends the E-table above).**

| Capability | Shown as |
|---|---|
| Durable-agent runtime (Vercel Workflows **or** eve) | The campaign that runs itself — resumes after a crash mid-expedition |
| MA-3 `prime` (auto-kept) | Agent-to-agent handoff with **no human curating the budget** |
| MA-2 `read_agent_ids` fan-in | The `converge()` chronicler reading K parallel heroes at once |
| Fluid Compute | Each autonomous hero's turn loop, running warm after the response |

**Acceptance.** A campaign started with one API call plays ≥3 expeditions to a win with
no human input; killing the deployment mid-expedition and redeploying resumes the same
campaign (durable-step proof); the spectator UI shows the map/dossier filling across
heroes. **Gating:** opt-in (`GUILD_AUTONOMOUS=true`); the human-driven loop (E-1..E-5)
remains the default demo. **Risk to manage:** cost/runaway loops — cap total expeditions
and torch per campaign; every LLM call is a metered agent turn.

**Dependencies.** E-1..E-4 (the whole lifecycle + win condition), MA-1/2/3/4 (all landed),
Fluid Compute (done). New: a durable-agent runtime — `@vercel/workflow` (or the current
Workflows SDK) **or** `eve` (beta; pin the version) — plus a campaign store (can be the
memory core itself — a `campaigns` tenant — keeping "no separate state" intact; do not let
eve's own agent-state stand in for the guild ledger).

---

### E-7 — Vercel OIDC auth for the dungeon→core hop *(integration hardening, not a game feature)*

**Problem.** The dungeon authenticates to the core with a **static shared secret** —
`CORE_API_KEY` sent as `Authorization: Bearer` (`lib/core.ts`). The same long-lived key
lives in both the Vercel project env and the core env; rotating it means editing two places,
and a leak grants standing access. The core already speaks OIDC (JWT-1/2/3 landed) — the
dungeon just isn't using it.

**Design.** Replace the static key with **short-lived, per-request Vercel OIDC tokens**,
verified by the core's existing OIDC path. Nothing new on the core *code* side — it's config
+ a client-side swap:
- **Dungeon side.** In `coreFetch` (`lib/core.ts`), mint a token per request with
  `getVercelOidcToken()` from `@vercel/functions` and send it as the bearer, replacing the
  `CORE_API_KEY` header. Falls back to the static key when the OIDC token is absent (local
  `next dev` without the Vercel OIDC env) so local play still works.
- **Core side.** Set `OIDC_ISSUER` to Vercel's issuer (`https://oidc.vercel.com/<team>`) and
  the expected `aud`; the core verifies the JWT against the issuer's JWKS (`security/auth.py`,
  JWT-1) and maps claims → tenant/scope (JWT-2). Confirm Vercel's token claim shape maps to a
  sensible `tenant_id`/`access_level` — may need a small claims-mapping tweak, the one piece
  that could touch core code.
- **Keep the static key as the local/dev + CI credential** (the core stays keyless by default
  for tests); OIDC is the production dungeon→core credential only.

**Why it's worth it.** Removes the shared long-lived secret from two envs; tokens are
short-TTL and rotate automatically; identity is per-deployment. It's the one place Vercel and
the FastAPI core genuinely meet — using machinery **both sides already have** — as opposed to
hosting the core on Vercel, which its durable write worker + in-process Faiss index make a
non-starter (the core stays a long-lived container; see the dungeon README).

**Acceptance.** With `OIDC_ISSUER` set on the core, the deployed dungeon reaches the core
with **no `CORE_API_KEY`** present; requests carry a Vercel-minted JWT; an expired/forged
token is rejected (401). Local `next dev` still works via the static-key fallback. **Gating:**
production-only; the keyless local/CI path is unchanged.

**Dependencies.** Core JWT-1/2/3 (landed). New: `@vercel/functions` (already a dep via Fluid
Compute's `waitUntil` usage, if adopted) + Vercel OIDC enabled on the project. Independent of
E-1..E-6.

---

## Explicitly out of scope (decided)

- **Multiplayer / multiple concurrent heroes** — one hero at a time keeps the handoff
  legible; concurrent agents are the *eval's* job (MA-5), not the demo's. (E-6 revisits
  this as an opt-in, autonomous post-1.0 showcase, with bounded parallelism only.)
- **Procedural dungeon generation** — authored content is what makes the traitor arc
  provable (E-4's content test); generation would trade the demo's point for replay
  value it doesn't need.
- **Combat system** — perish/torch are the only fail states; this is a detective game
  wearing a dungeon crawl's clothes.
- **Server-side game state** — GameState stays client-held (as today); the *memory* is
  the server state, which is the point.
