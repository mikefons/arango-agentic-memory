/** Shared types for the Due-Diligence Room ↔ core boundary (docs/DILIGENCE.md). */

/** Tenant + agent context for a core call. One Room == one tenant (`room:<id>`). */
export interface Ctx {
  tenant_id: string;
  agent_id: string;
  read_agent_ids?: string[];
  access_level?: "read" | "write";
}

/** A specialist agent's mandate. Its id becomes the memory `agent_id` (MA-2 provenance). */
export type SpecialistId =
  | "financial"
  | "legal"
  | "technical"
  | "market"
  | "redteam"
  | "synthesis";

/** The shared read namespace every agent can see (mirrors the CrewAI/guild convention). */
export const SHARED_AGENT = "diligence::shared";

/** A claim written to memory: subject/predicate/value plus provenance + as-of time. */
export interface Claim {
  subject: string;
  predicate: string;
  value: string;
  source: string;
  /** 0..1 prior trust for the source type (filing > news > management claim). */
  source_reliability: number;
  /** ISO instant the claim is asserted to hold as-of (bi-temporal, §4/§12). */
  as_of?: string;
}

export interface StoreResult {
  episode_id: string;
  memory_ids: string[];
  entity_ids: string[];
}

export interface RetrieveHit {
  text: string;
  score: number;
  source: string;
  agent_id?: string;
}

export interface RetrieveResult {
  context: string;
  hits: RetrieveHit[];
  tokens_injected: number;
}

export interface FlushResult {
  status: string;
  pending: number;
}
