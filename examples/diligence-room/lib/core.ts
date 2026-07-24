/**
 * Typed server-side client over the Python core's /v1 HTTP API, scoped to a
 * Due-Diligence **Room** (one tenant == `room:<id>`, never the dungeon's tenant).
 *
 * Runs only on the server (route handlers / server actions / the campaign worker) —
 * never ship the core URL or raw memory to the browser. Adapted from the dungeon's
 * `lib/core.ts` (copied deliberately; see docs/DILIGENCE.md "copy, don't extract").
 *
 * DR-0a ships `health()` + the pure tenant/URL helpers and thin, typed wrappers over
 * the verbs DR-1 needs (`storeClaim`, `retrieve`, `prime`, `flush`, `memoryGraph`).
 */

import type { Claim, Ctx, FlushResult, PrimeResult, RetrieveResult, StoreResult } from "./types";
import { SHARED_AGENT, SPECIALIST_AGENTS, TEAM_AGENTS } from "./types";
import { claimText } from "./claims";

// Default to 127.0.0.1 (not "localhost"): server-side fetch can resolve "localhost"
// to IPv6 ::1, which a Docker-published (IPv4) core won't answer.
const CORE_URL = normalizeCoreUrl(process.env.CORE_URL ?? "http://127.0.0.1:8080");
const CORE_API_KEY = process.env.CORE_API_KEY; // bearer key when the core enforces auth (§17)
// Per-request timeout. 8s is fine for a local core, but a hosted core doing extraction +
// embedding on a read/consolidate call can take longer — override with CORE_TIMEOUT_MS.
const DEFAULT_TIMEOUT_MS = Number(process.env.CORE_TIMEOUT_MS ?? 30000);

/** Strip any trailing slashes so `${CORE_URL}/path` never yields `//path` → 404. */
export function normalizeCoreUrl(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Map a Room id to its isolated core tenant. Never collides with other demos' tenants. */
export function roomTenant(roomId: string): string {
  const slug = roomId.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  return `room:${slug || "untitled"}`;
}

async function coreFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${CORE_URL}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(CORE_API_KEY ? { authorization: `Bearer ${CORE_API_KEY}` } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
    signal: init?.signal ?? AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    throw new Error(`core ${path} → ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

const qs = (params: Record<string, string | undefined>) =>
  new URLSearchParams(
    Object.entries(params).filter((e): e is [string, string] => e[1] != null),
  ).toString();

/** True when the core is reachable and healthy. Never throws. */
export async function health(): Promise<boolean> {
  try {
    await coreFetch("/health", { signal: AbortSignal.timeout(4000) });
    return true;
  } catch {
    return false;
  }
}

/**
 * Store a specialist's claim into the Room's shared memory under its own `agent_id`
 * (MA-2 provenance) with source reliability (CC-*). `sync` forces read-your-writes
 * (MA-1) so a following retrieve/red-team pass sees it. Fleshed out further in DR-0c/DR-1.
 */
export function storeClaim(
  roomId: string,
  agentId: string,
  claim: Claim,
  opts?: { sync?: boolean },
): Promise<StoreResult> {
  return coreFetch<StoreResult>("/v1/store", {
    method: "POST",
    body: JSON.stringify({
      content: claimText(claim),
      ctx: { tenant_id: roomTenant(roomId), agent_id: agentId, access_level: "write" },
      // Reliability weights belief (CC-*); kept out of the text so it doesn't pollute extraction.
      source_reliability: claim.source_reliability,
      sync: opts?.sync ?? true,
    }),
  });
}

/** Retrieve across specialists for the Room (MA-2 `read_agent_ids`); shared tier included. */
export function retrieve(
  roomId: string,
  query: string,
  agentId: string,
  readAgentIds?: string[],
  k = 10,
): Promise<RetrieveResult> {
  const ctx: Ctx = {
    tenant_id: roomTenant(roomId),
    agent_id: agentId,
    read_agent_ids: readAgentIds ?? [agentId, SHARED_AGENT],
    access_level: "read",
  };
  return coreFetch<RetrieveResult>("/v1/retrieve", {
    method: "POST",
    body: JSON.stringify({ query, ctx, opts: { mode: "lite", k } }),
  });
}

/**
 * Gather the claims all specialists have written for the Room — the red-team's input.
 * Reads across every specialist's agent_id plus the shared tier (MA-2), pulling a wide pool.
 */
export function gatherClaims(roomId: string, query: string, k = 60): Promise<RetrieveResult> {
  return retrieve(roomId, query, "redteam", [...SPECIALIST_AGENTS, SHARED_AGENT], k);
}

/** Read-your-writes barrier for the Room (MA-1): block until queued writes land. */
export function flush(roomId: string, agentId: string, timeoutMs = 8000): Promise<FlushResult> {
  return coreFetch<FlushResult>("/v1/flush", {
    method: "POST",
    body: JSON.stringify({
      ctx: { tenant_id: roomTenant(roomId), agent_id: agentId },
      timeout_ms: timeoutMs,
    }),
    // The core blocks up to timeout_ms draining the queue; the client abort must outlast it.
    signal: AbortSignal.timeout(timeoutMs + 2000),
  });
}

/**
 * Assemble a handoff briefing for the synthesis agent (MA-3): the whole team's findings —
 * specialists + the red-team's disputes + the shared tier — packed under a token budget.
 * This is the reasoning the memo is written from.
 */
export function prime(roomId: string, task: string): Promise<PrimeResult> {
  return coreFetch<PrimeResult>("/v1/prime", {
    method: "POST",
    body: JSON.stringify({
      task,
      ctx: {
        tenant_id: roomTenant(roomId),
        agent_id: "synthesis",
        read_agent_ids: ["synthesis", ...TEAM_AGENTS],
        access_level: "read",
      },
      opts: { mode: "lite", k: 40, max_memory_tokens: 3000 },
    }),
  });
}

/** The Room's evidence graph (entities + relations) for the war-room UI (DR-3). */
export function memoryGraph(roomId: string): Promise<unknown> {
  return coreFetch(`/v1/graph?${qs({ tenant_id: roomTenant(roomId) })}`);
}

export interface ResetResult {
  status: string;
  counts: Record<string, number>;
}

/**
 * Reset a Room's shared memory so a live run starts clean (soft-delete every claim + entity
 * under the Room's tenant, via the core's tenant-scoped `/v1/forget`). Only ever touches
 * `room:<id>` — never another demo's tenant, never drops a collection. Requires the write scope
 * the storeClaim key already has.
 */
export function resetRoom(roomId: string): Promise<ResetResult> {
  return coreFetch<ResetResult>("/v1/forget", {
    method: "POST",
    body: JSON.stringify({ tenant_id: roomTenant(roomId), access_level: "write" }),
  });
}

// ── Consolidation passes (the campaign's "reconcile" phase, §9/§13) ─────────
function consolidateCtx(roomId: string) {
  return { tenant_id: roomTenant(roomId), agent_id: "synthesis", access_level: "write" };
}

/** Recompute PageRank salience over the Room's entities (§9). */
export function salience(roomId: string): Promise<{ entities: number }> {
  return coreFetch("/v1/salience", {
    method: "POST",
    body: JSON.stringify({ ctx: consolidateCtx(roomId) }),
  });
}

/** Recompute label-propagation communities — clusters related parties (§9/§13). */
export function community(roomId: string): Promise<{ entities: number; communities: number }> {
  return coreFetch("/v1/community", {
    method: "POST",
    body: JSON.stringify({ ctx: consolidateCtx(roomId) }),
  });
}

/** Run Dream-State consolidation — confirm conflicts, distil summaries (§13). */
export function dream(roomId: string): Promise<unknown> {
  return coreFetch("/v1/dream", {
    method: "POST",
    body: JSON.stringify({ ctx: consolidateCtx(roomId) }),
  });
}
