/**
 * Pure types + formatting for the ontology-review UI (no React, so unit-testable).
 *
 * The core's ontology-evolution pass (DESIGN §13) proposes a typed relationship for
 * recurring `associated_with` clusters; a human approves/rejects here.
 */

export type ProposalStatus = "pending" | "approved" | "rejected";

export interface Proposal {
  _key: string;
  label_a: string;
  label_b: string;
  proposed_relationship: string;
  support: number;
  status: ProposalStatus;
  examples?: { a: string; b: string }[];
}

export interface ProposalList {
  enabled: boolean;
  proposals: Proposal[];
}

/** Human-readable one-liner: "Person → Company : works_at (7)". */
export function proposalSummary(p: Proposal): string {
  return `${p.label_a} → ${p.label_b} : ${p.proposed_relationship} (${p.support})`;
}
