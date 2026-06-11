/** Shapes mirroring the Python core's /v1 HTTP contract (DESIGN.md §19). */

export type AccessLevel = "read" | "write";

export interface Ctx {
  tenant_id: string;
  agent_id: string;
  session_id?: string;
  access_level?: AccessLevel;
}

export interface StoreResult {
  status: string;
  episode_id: string;
  memory_ids: string[];
}

export interface MemoryHit {
  text: string;
  score: number;
  source: string;
}

export interface RetrieveResult {
  context: string;
  hits: MemoryHit[];
  tokens_injected: number;
}

/** Entities never carry embeddings over the wire (§17 inversion defense). */
export interface Entity {
  id: string;
  name: string;
  label: string;
  source?: string;
  confidence?: number;
  needs_review?: boolean;
  conflict_with?: string | null;
  invalid_at?: string | null;
  valid_time?: string;
  valid_time_explicit?: boolean;
}

export interface EntityDetail {
  entity: Entity;
  related: Array<Entity & { relationship?: string }>;
}

export interface Step {
  tool_name: string;
  arguments: Record<string, unknown>;
  outcome: string;
  use_count: number;
}

export interface RetrieveOpts {
  mode?: "lite" | "full";
  max_memory_tokens?: number;
  k?: number;
}

export interface DreamResult {
  reviewed: number;
  superseded: number;
  consolidated: number;
  cleared: number;
  breaker_tripped: boolean;
}
