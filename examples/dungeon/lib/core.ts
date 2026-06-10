/**
 * Typed server-side client over the Python core's /v1 HTTP API.
 *
 * Runs only on the server (route handlers / server actions) — never ship the
 * core URL or raw memory to the browser. The DM agent's conversational memory
 * is handled by the `arangoMemory()` middleware (3.5c-1); these helpers are for
 * explicit game actions (rooms, NPCs, testimony, the lie engine).
 */

import type {
  Ctx,
  Entity,
  EntityDetail,
  RetrieveOpts,
  RetrieveResult,
  Step,
  StoreResult,
} from "./types";

// Default to 127.0.0.1 (not "localhost"): server-side fetch can resolve
// "localhost" to IPv6 ::1, which a Docker-published (IPv4) core won't answer —
// the request then hangs. Forcing IPv4 avoids that.
const CORE_URL = process.env.CORE_URL ?? "http://127.0.0.1:8080";
const DEFAULT_TIMEOUT_MS = 8000;

async function coreFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Always bound the request so a slow/unreachable core can't hang the UI (§15).
  const res = await fetch(`${CORE_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
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

export async function health(): Promise<boolean> {
  try {
    await coreFetch("/health", { signal: AbortSignal.timeout(4000) });
    return true;
  } catch {
    return false;
  }
}

export function store(content: string, ctx: Ctx, opts?: { mode?: "lite" | "full" }): Promise<StoreResult> {
  return coreFetch<StoreResult>("/v1/store", {
    method: "POST",
    body: JSON.stringify({ content, ctx: { access_level: "write", ...ctx }, opts }),
  });
}

export function retrieve(query: string, ctx: Ctx, opts?: RetrieveOpts): Promise<RetrieveResult> {
  return coreFetch<RetrieveResult>("/v1/retrieve", {
    method: "POST",
    body: JSON.stringify({ query, ctx: { access_level: "read", ...ctx }, opts }),
  });
}

export function getEntity(entityId: string, tenantId: string): Promise<EntityDetail> {
  return coreFetch<EntityDetail>(`/v1/entity?${qs({ entity_id: entityId, tenant_id: tenantId })}`);
}

export function listEntities(
  tenantId: string,
  filter?: { agent_id?: string; label?: string },
): Promise<{ entities: Entity[] }> {
  return coreFetch(`/v1/entities?${qs({ tenant_id: tenantId, ...filter })}`);
}

export function recordStep(
  args: { tool_name: string; arguments: Record<string, unknown>; outcome: string; prev_step_key?: string },
  ctx: Ctx,
): Promise<{ status: string; step_id?: string }> {
  return coreFetch("/v1/step", {
    method: "POST",
    body: JSON.stringify({ ...args, ctx: { access_level: "write", ...ctx } }),
  });
}

export function getSteps(tenantId: string, agentId: string): Promise<{ steps: Step[] }> {
  return coreFetch(`/v1/steps?${qs({ tenant_id: tenantId, agent_id: agentId })}`);
}
