/**
 * Turn a completed run into the ordered event sequence the War Room streams (DR-3a).
 * Pure — the SSE route paces these out with delays (canned) or emits them as a live campaign
 * produces them. Keeping it pure makes the event ordering unit-testable.
 */

import type { CampaignEvent } from "./room-state";
import type { GoldenRun } from "./fixtures/golden/run";

/**
 * The canonical event order: each step in turn, the red-team's disputes right after the
 * `redteam` step, the memo after `synthesis`, then `done`. This is exactly the order a live
 * campaign emits, so the client renders identically in canned and live modes.
 */
export function goldenEvents(run: GoldenRun): CampaignEvent[] {
  const events: CampaignEvent[] = [];
  for (const step of run.steps) {
    events.push({ type: "step", step });
    if (step.name === "redteam" && step.status === "ok") {
      events.push({ type: "disputes", disputes: run.disputes });
    }
    if (step.name === "synthesis" && step.status === "ok") {
      events.push({ type: "memo", memo: run.memo });
    }
  }
  events.push({ type: "done", ok: run.steps.every((s) => s.status !== "error") });
  return events;
}
