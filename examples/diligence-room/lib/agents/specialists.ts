/**
 * Specialist registry (DR-1). Each specialist reads its slice of the data room with a mandate
 * focused on its discipline. DR-1a ships Financial; DR-1b adds Legal, Technical, Market.
 */

import type { SpecialistId } from "../types";
import { docsForSpecialist } from "../fixtures";
import type { SpecialistConfig } from "./types";

const CONFIGS: Partial<Record<SpecialistId, Omit<SpecialistConfig, "docs">>> = {
  financial: {
    id: "financial",
    title: "Financial analyst",
    mandate:
      "Extract financial claims: revenue/ARR, growth, margins, retention/churn, customer " +
      "concentration, and any related-party revenue. Capture the exact figures and the entity " +
      "each figure describes.",
  },
};

/** The built specialist config for `id` (with its documents), or undefined if not yet defined. */
export function specialist(id: SpecialistId): SpecialistConfig | undefined {
  const base = CONFIGS[id];
  if (!base) return undefined;
  return { ...base, docs: docsForSpecialist(id) };
}

/** All specialists currently wired (grows with DR-1b). */
export function specialists(): SpecialistConfig[] {
  return (Object.keys(CONFIGS) as SpecialistId[])
    .map(specialist)
    .filter((c): c is SpecialistConfig => c !== undefined);
}
