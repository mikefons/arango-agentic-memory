/**
 * Guided narration (DR-3f) — pure mapping from war-room state to a running commentary that
 * steps a first-time viewer through the demo. It keys off the live phase cursor so the ribbon
 * always describes what is happening *right now*, and calls out the memory capability each phase
 * exercises (shared write, consolidation, cross-examination, prime → memo).
 */

import type { RoleId } from "./agents-meta";
import type { Phase, WarState } from "./campaign-state";
import { PHASES } from "./campaign-state";

export interface Narration {
  /** The main sentence — what's happening now. */
  line: string;
  /** The memory capability this step demonstrates (the "why it matters"). */
  note: string;
  /** Agent whose colour tints the ribbon, if this step belongs to one. */
  agent: RoleId | null;
  /** 0-based progress through the pipeline (for the ribbon's step counter). */
  step: number;
  /** Total steps. */
  total: number;
}

const PHASE_SCRIPT: Record<Phase, { line: string; note: string; agent: RoleId | null }> = {
  "specialist:financial": {
    line: "The Financial analyst is reading the data room — revenue, retention, related-party revenue.",
    note: "Each claim is written to shared memory tagged with its source's reliability.",
    agent: "financial",
  },
  "specialist:legal": {
    line: "The Legal analyst is reading litigation, contracts, IP, and corporate structure.",
    note: "Independent agent, same shared memory — no analyst sees another's raw work yet.",
    agent: "legal",
  },
  "specialist:technical": {
    line: "The Technical analyst is checking capabilities, IP provenance, and measured uptime.",
    note: "Belief is corroboration-weighted: audited sources outrank a pitch deck.",
    agent: "technical",
  },
  "specialist:market": {
    line: "The Market analyst is sizing customers, deals, and competitive position.",
    note: "Four agents have now written claims into one shared, bi-temporal graph.",
    agent: "market",
  },
  "flush:specialists": {
    line: "Flushing the write barrier — every specialist's claim is now durably visible.",
    note: "read-your-writes: the next agents are guaranteed to see everything just written.",
    agent: null,
  },
  consolidate: {
    line: "Consolidating shared memory — salience, communities, and a dream-state review.",
    note: "The graph self-organizes: related parties fall into the same community.",
    agent: null,
  },
  redteam: {
    line: "The Red-team is cross-examining the whole shared memory at once.",
    note: "The payoff of shared memory: contradictions no single specialist could see.",
    agent: "redteam",
  },
  "flush:redteam": {
    line: "Flushing the red-team's findings back into shared memory.",
    note: "Disputes are captured as first-class memory, ready for synthesis to inherit.",
    agent: null,
  },
  synthesis: {
    line: "Synthesis primes across the whole team and writes the investment memo.",
    note: "One budgeted briefing (prime) → an evidence-chained memo, not a guess.",
    agent: "synthesis",
  },
};

/** The phase currently running (the live cursor), or null. */
function runningPhase(state: WarState): Phase | null {
  return PHASES.find((p) => state.phases[p] === "running") ?? null;
}

/** Map the current war-room state to the guided-narration ribbon. */
export function narrate(state: WarState): Narration {
  const total = PHASES.length;

  if (state.run === "idle") {
    return {
      line: "Press Run to send four specialist agents into Northwind's data room.",
      note: "They share one memory. Watch it catch what no single reviewer would.",
      agent: null,
      step: 0,
      total,
    };
  }

  if (state.run === "done") {
    const rec = state.memo?.recommendation.replace(/_/g, " ") ?? "done";
    const risks = state.disputes.length;
    return {
      line: `Verdict: ${rec} — ${risks} contradiction${risks === 1 ? "" : "s"} surfaced from shared memory.`,
      note: "Open the memo: every finding traces back through its evidence chain.",
      agent: "synthesis",
      step: total,
      total,
    };
  }

  if (state.run === "error") {
    return {
      line: "The campaign stopped early.",
      note: "A step failed — check the pipeline for the phase that errored.",
      agent: null,
      step: PHASES.filter((p) => state.phases[p] === "done").length,
      total,
    };
  }

  // running
  const phase = runningPhase(state);
  const done = PHASES.filter((p) => state.phases[p] === "done").length;
  if (!phase) {
    return { line: "Working…", note: "", agent: null, step: done, total };
  }
  const script = PHASE_SCRIPT[phase];
  return { ...script, step: done, total };
}
