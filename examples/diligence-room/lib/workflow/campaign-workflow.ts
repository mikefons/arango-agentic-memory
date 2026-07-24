/**
 * DR-6a (PoC) — the campaign as a Vercel Workflow. Each phase becomes a durable `'use step'`,
 * so the run is no longer bounded by a single serverless function's timeout (the 300s kill).
 * The orchestration mirrors `runCampaign` (specialists → flush → consolidate → red-team →
 * flush → synthesis); progress is surfaced via a run-state store keyed by runId (DR-6b).
 *
 * This is the skeleton: it wires the real live steps behind durable-step boundaries. Observing
 * progress (status route) + the run-state store land in DR-6b.
 */

import { specialists } from "../agents/specialists";
import {
  liveConsolidate,
  liveFlush,
  liveRedTeam,
  liveSpecialist,
  liveSynthesis,
} from "../agents/live";
import type { SpecialistConfig } from "../agents/types";

async function specialistStep(roomId: string, config: SpecialistConfig): Promise<number> {
  "use step";
  const { claimsWritten } = await liveSpecialist(roomId, config);
  return claimsWritten;
}

async function flushStep(roomId: string): Promise<void> {
  "use step";
  await liveFlush(roomId);
}

async function consolidateStep(roomId: string): Promise<void> {
  "use step";
  await liveConsolidate(roomId);
}

async function redTeamStep(roomId: string): Promise<number> {
  "use step";
  const { disputes } = await liveRedTeam(roomId);
  return disputes.length;
}

async function synthesisStep(roomId: string): Promise<{ findings: number; recommendation: string }> {
  "use step";
  const memo = await liveSynthesis(roomId);
  return { findings: memo.findings.length, recommendation: memo.recommendation };
}

/** The durable campaign. Runs each phase as an independently-checkpointed step. */
export async function runCampaignWorkflow(roomId: string): Promise<{ disputes: number; findings: number }> {
  "use workflow";

  for (const s of specialists()) {
    await specialistStep(roomId, s);
  }
  await flushStep(roomId);
  await consolidateStep(roomId);
  const disputes = await redTeamStep(roomId);
  await flushStep(roomId);
  const memo = await synthesisStep(roomId);

  return { disputes, findings: memo.findings };
}
