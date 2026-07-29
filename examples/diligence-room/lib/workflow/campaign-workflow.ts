/**
 * DR-6 — the campaign as a Vercel Workflow. Each phase is a durable `'use step'`, so the run is
 * no longer bounded by a single serverless function's 300s limit. Each step writes its
 * `CampaignEvent`s to the run's durable stream (`getWritable`); the stream route reads them from
 * `getRun(runId).readable` (DR-6b) and the client folds them through the same reducer the canned
 * path uses (DR-6c). Orchestration mirrors `runCampaign`.
 */

import { getWritable } from "workflow";
import { specialists } from "../agents/specialists";
import {
  liveConsolidate,
  liveFlush,
  liveRedTeam,
  liveSpecialist,
  liveSynthesis,
} from "../agents/live";
import type { SpecialistConfig } from "../agents/types";
import type { CampaignEvent } from "../room-state";

const encoder = new TextEncoder();

/** Append one CampaignEvent to the run's durable stream (NDJSON). Must run inside a step. */
async function emit(ev: CampaignEvent): Promise<void> {
  const writer = getWritable().getWriter();
  try {
    await writer.write(encoder.encode(`${JSON.stringify(ev)}\n`));
  } finally {
    writer.releaseLock();
  }
}

function msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function specialistStep(roomId: string, config: SpecialistConfig): Promise<void> {
  "use step";
  try {
    const { claimsWritten } = await liveSpecialist(roomId, config);
    await emit({ type: "step", step: { name: `specialist:${config.id}`, status: "ok", detail: `${claimsWritten} claim(s)` } });
  } catch (e) {
    // A specialist failure is non-fatal (matches runCampaign) — record it and carry on.
    await emit({ type: "step", step: { name: `specialist:${config.id}`, status: "error", detail: msg(e) } });
  }
}

async function flushStep(roomId: string, phase: "flush:specialists" | "flush:redteam"): Promise<void> {
  "use step";
  await liveFlush(roomId);
  await emit({ type: "step", step: { name: phase, status: "ok" } });
}

async function consolidateStep(roomId: string): Promise<void> {
  "use step";
  await liveConsolidate(roomId);
  await emit({ type: "step", step: { name: "consolidate", status: "ok" } });
}

/** Cross-examine shared memory; emit the disputes. Returns whether the phase succeeded. */
async function redTeamStep(roomId: string): Promise<boolean> {
  "use step";
  try {
    const { disputes } = await liveRedTeam(roomId);
    await emit({ type: "step", step: { name: "redteam", status: "ok", detail: `${disputes.length} dispute(s)` } });
    await emit({ type: "disputes", disputes });
    return true;
  } catch (e) {
    await emit({ type: "step", step: { name: "redteam", status: "error", detail: msg(e) } });
    return false;
  }
}

async function synthesisStep(roomId: string): Promise<void> {
  "use step";
  try {
    const memo = await liveSynthesis(roomId);
    await emit({ type: "step", step: { name: "synthesis", status: "ok", detail: `${memo.findings.length} finding(s) → ${memo.recommendation}` } });
    await emit({ type: "memo", memo });
  } catch (e) {
    // Non-fatal: a slow/failed memo shouldn't stall the run — record it and still finish.
    await emit({ type: "step", step: { name: "synthesis", status: "error", detail: msg(e) } });
  }
}

async function finishStep(ok: boolean): Promise<void> {
  "use step";
  await emit({ type: "done", ok });
}

/** The durable campaign. Each phase is an independently-checkpointed step. */
export async function runCampaignWorkflow(roomId: string): Promise<{ ok: boolean }> {
  "use workflow";

  for (const s of specialists()) {
    await specialistStep(roomId, s);
  }
  await flushStep(roomId, "flush:specialists");
  await consolidateStep(roomId);

  const redOk = await redTeamStep(roomId);
  await flushStep(roomId, "flush:redteam");
  if (redOk) {
    await synthesisStep(roomId);
  }

  await finishStep(redOk);
  return { ok: redOk };
}
