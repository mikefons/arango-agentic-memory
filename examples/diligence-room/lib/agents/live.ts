/**
 * Live wiring (DR-2) — binds the agent orchestrators to the real model (getModel) and the real
 * core client. These are the concrete step functions the campaign route injects into
 * `runCampaign`. Kept out of the campaign logic so that logic stays LLM-/core-agnostic and
 * unit-testable.
 */

import { getModel } from "../model";
import { claimFromDoc } from "../claims";
import {
  community,
  dream,
  flush,
  gatherClaims,
  prime,
  salience,
  storeClaim,
} from "../core";
import type { Claim } from "../types";
import type { SpecialistConfig } from "./types";
import { makeExtractor } from "./extract";
import { runSpecialist } from "./specialist";
import { disputeText, makeDisputeFinder, runRedTeam } from "./redteam";
import { makeMemoWriter, memoHeadline, runSynthesis, type Memo } from "./synthesis";

const GATHER_QUERY =
  "Northwind Robotics revenue ARR retention churn customers litigation contracts ownership " +
  "related party technology uptime market footprint";

const SYNTHESIS_TASK =
  "Assess whether to invest in Northwind Robotics. Weigh the specialists' findings and the " +
  "red-team's disputes; flag material risks and give a recommendation.";

// Async writes: specialists/red-team enqueue claims (fast) and the campaign's flush barrier
// drains them before anyone reads (MA-1). This keeps a hosted-core live run from blocking on
// per-claim extraction + embedding — the difference between fitting the function budget or not.
const FLUSH_TIMEOUT_MS = Number(process.env.DILIGENCE_FLUSH_MS ?? 90000);

/** Run one specialist with the real extractor + async store (drained at flush:specialists). */
export function liveSpecialist(roomId: string, config: SpecialistConfig) {
  const extract = makeExtractor(getModel());
  return runSpecialist(config, {
    extract,
    store: async (agentId, doc, triple) => {
      await storeClaim(roomId, agentId, claimFromDoc(doc, triple), { sync: false });
    },
  });
}

/** Reconcile the Room's memory: salience + communities (best-effort so a slow pass can't
 *  stall the run). Dream-State is the heaviest pass — opt in with DILIGENCE_DREAM=1. */
export async function liveConsolidate(roomId: string): Promise<void> {
  await salience(roomId).catch(() => undefined);
  await community(roomId).catch(() => undefined);
  if (process.env.DILIGENCE_DREAM === "1") {
    await dream(roomId).catch(() => undefined);
  }
}

/** Cross-examine shared memory and record the red-team's disputes. */
export async function liveRedTeam(roomId: string) {
  const pool = await gatherClaims(roomId, GATHER_QUERY);
  const claims = pool.hits.map((h) => ({ text: h.text, agent_id: h.agent_id }));
  return runRedTeam({
    claims,
    find: makeDisputeFinder(getModel()),
    record: async (d) => {
      const finding: Claim = {
        subject: d.subject,
        predicate: `dispute:${d.kind}`,
        value: disputeText(d),
        source: "red-team analysis",
        source_reliability: d.confidence,
      };
      await storeClaim(roomId, "redteam", finding, { sync: false });
    },
  });
}

/** Prime the team briefing, write the memo, and capture the verdict (MA-4). */
export async function liveSynthesis(roomId: string): Promise<Memo> {
  const briefing = await prime(roomId, SYNTHESIS_TASK);
  const memo = await runSynthesis(briefing.context, makeMemoWriter(getModel()));
  const verdict: Claim = {
    subject: memo.target,
    predicate: "memo:verdict",
    value: memoHeadline(memo),
    source: "synthesis",
    source_reliability: 0.9,
  };
  await storeClaim(roomId, "synthesis", verdict, { sync: false });
  return memo;
}

/** The read-your-writes barrier bound to a Room — generous timeout so the queue actually
 *  drains (all extraction done) before the next phase reads. */
export function liveFlush(roomId: string): Promise<void> {
  return flush(roomId, "synthesis", FLUSH_TIMEOUT_MS).then(() => undefined);
}
