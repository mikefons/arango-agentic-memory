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

/** Emit a step event with its wall-clock duration, and log it so Vercel's runtime logs show
 *  where a live run spends its time. */
async function emitStep(name: string, status: "ok" | "error", t0: number, detail?: string): Promise<void> {
  const ms = Date.now() - t0;
  console.log(`[campaign] ${name} ${status} ${ms}ms${detail ? ` — ${detail}` : ""}`);
  await emit({ type: "step", step: { name, status, detail, ms } });
}

async function specialistStep(roomId: string, config: SpecialistConfig): Promise<void> {
  "use step";
  const t0 = Date.now();
  try {
    const { claimsWritten } = await liveSpecialist(roomId, config);
    await emitStep(`specialist:${config.id}`, "ok", t0, `${claimsWritten} claim(s)`);
  } catch (e) {
    // A specialist failure is non-fatal (matches runCampaign) — record it and carry on.
    await emitStep(`specialist:${config.id}`, "error", t0, msg(e));
  }
}

async function flushStep(roomId: string, phase: "flush:specialists" | "flush:redteam"): Promise<void> {
  "use step";
  const t0 = Date.now();
  // Best-effort barrier: the flush is a read-your-writes convenience, not a hard gate. A slow or
  // wedged flush must never stall the durable run — bound it, and advance regardless.
  try {
    await liveFlush(roomId);
  } catch {
    // queue still draining or the call timed out — the next phase reads what has landed
  }
  await emitStep(phase, "ok", t0);
}

async function consolidateStep(roomId: string): Promise<void> {
  "use step";
  const t0 = Date.now();
  await liveConsolidate(roomId);
  await emitStep("consolidate", "ok", t0);
}

/** Cross-examine shared memory; emit the disputes. Returns whether the phase succeeded. */
async function redTeamStep(roomId: string): Promise<boolean> {
  "use step";
  const t0 = Date.now();
  try {
    const { disputes } = await liveRedTeam(roomId);
    await emitStep("redteam", "ok", t0, `${disputes.length} dispute(s)`);
    await emit({ type: "disputes", disputes });
    return true;
  } catch (e) {
    await emitStep("redteam", "error", t0, msg(e));
    return false;
  }
}

async function synthesisStep(roomId: string): Promise<void> {
  "use step";
  const t0 = Date.now();
  try {
    const memo = await liveSynthesis(roomId);
    await emitStep("synthesis", "ok", t0, `${memo.findings.length} finding(s) → ${memo.recommendation}`);
    await emit({ type: "memo", memo });
  } catch (e) {
    // Non-fatal: a slow/failed memo shouldn't stall the run — record it and still finish.
    await emitStep("synthesis", "error", t0, msg(e));
  }
}

async function finishStep(ok: boolean): Promise<void> {
  "use step";
  await emit({ type: "done", ok });
}

/** The durable campaign. Each phase is an independently-checkpointed step. */
export async function runCampaignWorkflow(roomId: string): Promise<{ ok: boolean }> {
  "use workflow";

  // The four specialists read disjoint slices and write under their own agent ids, so they run
  // in parallel; the flush barrier below joins them before anyone reads shared memory.
  await Promise.all(specialists().map((s) => specialistStep(roomId, s)));
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
