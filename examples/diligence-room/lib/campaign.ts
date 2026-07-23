/**
 * The campaign orchestrator (DR-2) — one run that plays the whole deal:
 *
 *   dispatch specialists → flush → consolidate → red-team → flush → synthesis → memo
 *
 * Each phase is a discrete, idempotent step that records its status, so the sequence is
 * **durable-ready**: a Vercel Workflow wraps each `phase(...)` call as a `step.do(...)`
 * checkpoint (a crash resumes mid-campaign without re-running committed work). Here it runs
 * as a plain async sequence — same steps, same order — so it works locally and in CI. All
 * step functions are injected, so this is unit-testable without an LLM or a running core.
 */

import type { SpecialistConfig } from "./agents/types";
import type { Memo } from "./agents/synthesis";

export type StepStatus = "ok" | "skipped" | "error";

export interface CampaignStep {
  name: string;
  status: StepStatus;
  detail?: string;
}

export interface CampaignResult {
  room: string;
  steps: CampaignStep[];
  memo?: Memo;
}

export interface CampaignDeps {
  /** Run one specialist over its slice (extract + store). */
  runSpecialist: (config: SpecialistConfig) => Promise<{ claimsWritten: number }>;
  /** Read-your-writes barrier (MA-1). */
  flush: () => Promise<void>;
  /** Reconcile + rank: salience, communities, Dream-State (§9/§13). */
  consolidate: () => Promise<void>;
  /** Cross-examine shared memory for disputes (the red-team). */
  runRedTeam: () => Promise<{ disputes: unknown[] }>;
  /** Prime the team briefing and write the memo. */
  runSynthesis: () => Promise<Memo>;
  /** Optional: called as each step completes — the War Room streams these live (DR-3). */
  onStep?: (step: CampaignStep) => void;
}

/** Wrap a phase so any throw becomes a recorded error step instead of aborting the campaign. */
async function phase(
  steps: CampaignStep[],
  name: string,
  fn: () => Promise<string | void>,
  emit?: (step: CampaignStep) => void,
): Promise<boolean> {
  let step: CampaignStep;
  let ok: boolean;
  try {
    const detail = await fn();
    step = { name, status: "ok", detail: detail ?? undefined };
    ok = true;
  } catch (err) {
    step = { name, status: "error", detail: err instanceof Error ? err.message : String(err) };
    ok = false;
  }
  steps.push(step);
  emit?.(step);
  return ok;
}

export async function runCampaign(
  config: { roomId: string; specialists: SpecialistConfig[] },
  deps: CampaignDeps,
): Promise<CampaignResult> {
  const steps: CampaignStep[] = [];

  const emit = deps.onStep;

  // 1. Dispatch specialists (each an independent checkpoint).
  for (const s of config.specialists) {
    await phase(steps, `specialist:${s.id}`, async () => {
      const { claimsWritten } = await deps.runSpecialist(s);
      return `${claimsWritten} claim(s)`;
    }, emit);
  }

  // 2. Barrier — make all specialist writes visible before anyone reads them.
  await phase(steps, "flush:specialists", async () => void (await deps.flush()), emit);

  // 3. Consolidate — rank + cluster + reconcile the accumulated memory.
  await phase(steps, "consolidate", async () => void (await deps.consolidate()), emit);

  // 4. Red-team — cross-examine the shared picture for disputes.
  const redOk = await phase(steps, "redteam", async () => {
    const { disputes } = await deps.runRedTeam();
    return `${disputes.length} dispute(s)`;
  }, emit);

  // 5. Barrier — make the red-team's findings visible to synthesis.
  await phase(steps, "flush:redteam", async () => void (await deps.flush()), emit);

  // 6. Synthesis — prime the team and write the memo (only if we got this far).
  let memo: Memo | undefined;
  if (redOk) {
    await phase(steps, "synthesis", async () => {
      memo = await deps.runSynthesis();
      return `${memo.findings.length} finding(s) → ${memo.recommendation}`;
    }, emit);
  } else {
    const skip: CampaignStep = { name: "synthesis", status: "skipped", detail: "red-team failed" };
    steps.push(skip);
    emit?.(skip);
  }

  return { room: config.roomId, steps, memo };
}
