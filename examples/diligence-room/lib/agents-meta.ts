/** Display metadata for the six roles in the War Room agent rail (DR-3c). Pure — no server imports. */

export type RoleId = "financial" | "legal" | "technical" | "market" | "redteam" | "synthesis";

export interface RoleMeta {
  id: RoleId;
  title: string;
  /** One line: what this agent does. */
  blurb: string;
}

export const ROLES: RoleMeta[] = [
  { id: "financial", title: "Financial", blurb: "Revenue, retention, margins, related-party revenue" },
  { id: "legal", title: "Legal", blurb: "Litigation, contracts, IP, corporate structure" },
  { id: "technical", title: "Technical", blurb: "Capabilities, IP provenance, measured uptime" },
  { id: "market", title: "Market", blurb: "Customers, deal sizes, competitive position" },
  { id: "redteam", title: "Red-team", blurb: "Cross-examines shared memory for contradictions" },
  { id: "synthesis", title: "Synthesis", blurb: "Primes the team → the evidence-chained memo" },
];
