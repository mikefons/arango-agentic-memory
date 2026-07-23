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
  legal: {
    id: "legal",
    title: "Legal counsel",
    mandate:
      "Extract legal claims: litigation (pending, threatened, or filed), contracts and whether " +
      "they are binding or a non-binding LOI, IP ownership, corporate structure, and any " +
      "related-party relationships. Capture who asserts each and whether it is disputed.",
  },
  technical: {
    id: "technical",
    title: "Technical due-diligence lead",
    mandate:
      "Extract technical claims: product and platform capabilities, IP provenance " +
      "(proprietary vs open-source), and reliability/uptime figures. Capture whether each is a " +
      "marketing claim or an independently measured result, and the exact numbers.",
  },
  market: {
    id: "market",
    title: "Market analyst",
    mandate:
      "Extract market claims: customers and deal sizes, competitive position and market share, " +
      "and operational footprint (sites, regions). Capture the source and the exact figures, and " +
      "note when a claim is a rumor versus a confirmed fact.",
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
