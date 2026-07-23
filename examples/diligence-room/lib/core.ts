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

import type { Claim, Ctx, FlushResult, RetrieveResult, StoreResult } from "./types";
import { SHARED_AGENT, SPECIALIST_AGENTS } from "./types";
import { claimText } from "./claims";

// Default to 127.0.0.1 (not "localhost"): server-side fetch can resolve "localhost"
// to IPv6 ::1, which a Docker-published (IPv4) core won't answer.
const CORE_URL = normalizeCoreUrl(process.env.CORE_URL ?? "http://127.0.0.1:8080");
const CORE_API_KEY = process.env.CORE_API_KEY; // bearer key when the core enforces auth (§17)
const DEFAULT_TIMEOUT_MS = 8000;

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
  });
}

/** The Room's evidence graph (entities + relations) for the war-room UI (DR-3). */
export function memoryGraph(roomId: string): Promise<unknown> {
  return coreFetch(`/v1/graph?${qs({ tenant_id: roomTenant(roomId) })}`);
}
