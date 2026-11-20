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
import type { Claim, FlushResult } from "../types";
import type { SpecialistConfig } from "./types";
import { makeExtractor } from "./extract";
import { describeLlm, stageLine, type LlmCallStats } from "./llm-stats";
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
// Keep the flush short — a durable step should be a brief unit, not a 90s blocking poll. With the
// claim caps this is plenty of drain window; the barrier is best-effort anyway (see flushStep).
const FLUSH_TIMEOUT_MS = Number(process.env.DILIGENCE_FLUSH_MS ?? 30000);

// Claims go through the core's async write queue (drained at the flush barriers). Opt in to
// synchronous commits with DILIGENCE_SYNC_WRITES=1 — but don't, on the hosted core: every sync
// store forces an ArangoSearch view sync, and 16 concurrent ones saturated the core (all four
// specialists and the red-team timed out, 2026-11-20). The proper fix is a multi-worker write
// queue in the core (ROADMAP WQ-1); until then the async queue plus a patient flush is safer.
const SYNC_WRITES = process.env.DILIGENCE_SYNC_WRITES === "1";

/** Run one specialist with the real extractor + store (drained at flush:specialists). */
export function liveSpecialist(roomId: string, config: SpecialistConfig) {
  const extract = makeExtractor(getModel());
  return runSpecialist(config, {
    extract,
    store: async (agentId, doc, triple) => {
      await storeClaim(roomId, agentId, claimFromDoc(doc, triple), { sync: SYNC_WRITES });
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

// Bound the red-team's cross-examination pool. Its dispute-finder stuffs every claim into ONE
// prompt, so an unbounded pool (a live run can produce 100+ claims) makes that call crawl or
// wedge. Override with DILIGENCE_REDTEAM_K.
const REDTEAM_K = Number(process.env.DILIGENCE_REDTEAM_K ?? 24);

/** Cross-examine shared memory and record the red-team's disputes. */
export async function liveRedTeam(roomId: string) {
  const t0 = Date.now();
  const pool = await gatherClaims(roomId, GATHER_QUERY, REDTEAM_K);
  const gatherMs = Date.now() - t0;
  const claims = pool.hits.map((h) => ({ text: h.text, agent_id: h.agent_id }));
  const seen: { llm?: LlmCallStats } = {};
  const run = await runRedTeam({
    claims,
    find: makeDisputeFinder(getModel(), (s) => {
      seen.llm = s;
    }),
    record: async (d) => {
      const finding: Claim = {
        subject: d.subject,
        predicate: `dispute:${d.kind}`,
        value: disputeText(d),
        source: "red-team analysis",
        source_reliability: d.confidence,
      };
      await storeClaim(roomId, "redteam", finding, { sync: SYNC_WRITES });
    },
  });
  // Where this step's time goes: the core read, the model call, then recording the findings.
  const afterGather = Date.now() - t0 - gatherMs;
  console.log(
    stageLine("redteam", [
      ["gather", `${gatherMs}ms (${claims.length} claims)`],
      ["llm", seen.llm ? describeLlm(seen.llm) : "n/a (call failed)"],
      ["record+other", `${afterGather - (seen.llm?.ms ?? 0)}ms (${run.disputes.length} disputes)`],
    ]),
  );
  return run;
}

/** Prime the team briefing, write the memo, and capture the verdict (MA-4). */
export async function liveSynthesis(roomId: string): Promise<Memo> {
  const t0 = Date.now();
  // One retry: the briefing read can hit a core that is still busy with the preceding writes.
  let retried = false;
  const briefing = await prime(roomId, SYNTHESIS_TASK).catch((err) => {
    retried = true;
    console.warn("[campaign] prime failed, retrying once:", err instanceof Error ? err.message : err);
    return prime(roomId, SYNTHESIS_TASK);
  });
  const primeMs = Date.now() - t0;
  const seen: { llm?: LlmCallStats } = {};
  const memo = await runSynthesis(
    briefing.context,
    makeMemoWriter(getModel(), (s) => {
      seen.llm = s;
    }),
  );
  const tVerdict = Date.now();
  const verdict: Claim = {
    subject: memo.target,
    predicate: "memo:verdict",
    value: memoHeadline(memo),
    source: "synthesis",
    source_reliability: 0.9,
  };
  await storeClaim(roomId, "synthesis", verdict, { sync: false });
  console.log(
    stageLine("synthesis", [
      ["prime", `${primeMs}ms (${briefing.context.length} chars${retried ? ", retried" : ""})`],
      ["llm", seen.llm ? describeLlm(seen.llm) : "n/a"],
      ["verdict write", `${Date.now() - tVerdict}ms (${memo.findings.length} findings)`],
    ]),
  );
  return memo;
}

/** The read-your-writes barrier bound to a Room — generous timeout so the queue actually
 *  drains (all extraction done) before the next phase reads. */
export function liveFlush(roomId: string): Promise<FlushResult> {
  return flush(roomId, "synthesis", FLUSH_TIMEOUT_MS);
}
