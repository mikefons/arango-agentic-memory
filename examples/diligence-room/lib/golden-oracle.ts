/**
 * Golden-run oracle (DR-5a) — ties the deterministic golden fixture to the planted-defect
 * oracle (fixtures/DEFECTS), so the demo's canned replay and the golden reference cannot
 * silently drift from the contradictions they are supposed to surface. Pure + keyless, so it
 * gates in CI without a provider key or a running core.
 *
 * A live campaign is stochastic; the golden fixture is the reliable reference the stage replays
 * and the golden-run test checks. This module is what makes "reliably surfaces the planted
 * contradictions" a checkable property rather than a hope.
 */

import type { Defect } from "./fixtures/types";
import type { Dispute } from "./agents/redteam";
import { terms } from "./dispute-map";

export interface Coverage {
  /** Each planted defect paired with the golden dispute that surfaces it. */
  covered: { defect: Defect; dispute: Dispute }[];
  /** Defects with no matching dispute (by design: the "stale" belief-only signal). */
  uncovered: Defect[];
}

/** Do a defect and a dispute concern the same subject (discriminating-term overlap)? */
export function sameSubject(defect: Defect, dispute: Dispute): boolean {
  const want = terms(dispute.subject);
  for (const t of terms(defect.subject)) {
    if (want.has(t)) return true;
  }
  return false;
}

/**
 * Map each planted defect to the golden dispute that surfaces it, matching on kind *and*
 * subject so a defect can't be credited to an unrelated dispute of the same kind.
 */
export function goldenCoverage(defects: Defect[], disputes: Dispute[]): Coverage {
  const covered: Coverage["covered"] = [];
  const uncovered: Defect[] = [];
  for (const defect of defects) {
    const dispute = disputes.find((d) => d.kind === defect.kind && sameSubject(defect, d));
    if (dispute) covered.push({ defect, dispute });
    else uncovered.push(defect);
  }
  return { covered, uncovered };
}

/** Parse the leading integer out of a step detail like "6 dispute(s)" → 6. */
export function detailCount(detail?: string): number | null {
  const m = detail?.match(/^(\d+)/);
  return m ? Number(m[1]) : null;
}
