/**
 * @arango-memory/vercel — thin client middleware for the ArangoDB memory core.
 *
 * Holds NO memory logic. It (1) retrieves context from the Python core and
 * injects it before a turn, (2) durably stores the turn (user + assistant) after,
 * and (3) captures completed tool calls as procedural memory. Every core/network
 * failure degrades to a working, memory-less turn (DESIGN.md §15, §20).
 *
 * Also exports standalone `prime` (task briefing, MA-3) and `flush` (read-your-writes
 * barrier, MA-1) helpers for orchestrating multi-agent handoffs (DESIGN.md §14).
 *
 * Tool capture note: a LanguageModel middleware wraps a single model round-trip,
 * so a tool's *outcome* is only known once its result appears in a later turn's
 * prompt. We therefore record completed (call + result) pairs read from the
 * prompt history, de-duped by toolCallId — so there's a one-turn lag, by design.
 */

import type {
  LanguageModelV2CallOptions,
  LanguageModelV2Middleware,
  LanguageModelV2Prompt,
  LanguageModelV2StreamPart,
  LanguageModelV2ToolCallPart,
  LanguageModelV2ToolResultPart,
} from '@ai-sdk/provider';

/** Cap on a stored assistant response — long generations dilute retrieval (§16). */
const MAX_RESPONSE_CHARS = 4096;

export interface ArangoMemoryOptions {
  /** URL of the Python core service (DESIGN.md §20). */
  coreUrl: string;
  tenantId: string;
  agentId: string;
  sessionId?: string;
  /** Enrichment mode (DESIGN.md §10). Defaults to the core's configured mode. */
  mode?: 'lite' | 'full';
  maxMemoryTokens?: number;
  /** Read across these agents in one fused pass (MA-2) — e.g. own + shared crew tiers. */
  readAgentIds?: string[];
  /** Commit writes before responding so a handoff reader sees them (MA-1). Default false. */
  syncWrites?: boolean;
  /** Abort retrieval if the core is slow, so a turn never hangs on memory. */
  retrieveTimeoutMs?: number;
  /** Record completed tool calls as procedural memory (DESIGN.md §11). Default true. */
  captureToolTraces?: boolean;
  /** Store the model's response as memory too, not just the user turn (MA-4). Default true. */
  captureResponses?: boolean;
  /** Bearer API key for the core (DESIGN.md §17). Omit when the core runs open (keyless). */
  apiKey?: string;
}

/** Core request headers; adds `Authorization: Bearer` only when a key is configured. */
function jsonHeaders(apiKey?: string): Record<string, string> {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (apiKey) headers.authorization = `Bearer ${apiKey}`;
  return headers;
}

type Ctx = { tenant_id: string; agent_id: string; session_id?: string };

interface CompletedTool {
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
  outcome: 'success' | 'failure';
}

/** Per-middleware state so procedural writes aren't duplicated across turns. */
interface CaptureState {
  seen: Set<string>;
  lastStepKey?: string;
}

/** Extract the most recent user text from a model prompt (the retrieval cue). */
function lastUserText(prompt: LanguageModelV2Prompt): string {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const msg = prompt[i];
    if (msg.role !== 'user') continue;
    if (typeof msg.content === 'string') return msg.content;
    return msg.content
      .filter((p): p is { type: 'text'; text: string } => p.type === 'text')
      .map((p) => p.text)
      .join('\n');
  }
  return '';
}

/** Prepend a system message carrying retrieved memory context. */
function injectContext(
  params: LanguageModelV2CallOptions,
  context: string,
): LanguageModelV2CallOptions {
  if (!context) return params;
  const block = `[MEMORY CONTEXT]\n${context}\n[END MEMORY CONTEXT]`;
  return {
    ...params,
    prompt: [{ role: 'system', content: block }, ...params.prompt],
  };
}

function asArgs(input: unknown): Record<string, unknown> {
  return input !== null && typeof input === 'object' && !Array.isArray(input)
    ? (input as Record<string, unknown>)
    : { value: input };
}

/** Pair tool calls with their results from the prompt history → completed steps. */
function collectCompletedTools(prompt: LanguageModelV2Prompt): CompletedTool[] {
  const calls = new Map<string, LanguageModelV2ToolCallPart>();
  const completed: CompletedTool[] = [];
  for (const msg of prompt) {
    if (!Array.isArray(msg.content)) continue;
    for (const part of msg.content) {
      if (part.type === 'tool-call') {
        calls.set(part.toolCallId, part);
      } else if (part.type === 'tool-result') {
        const result = part as LanguageModelV2ToolResultPart;
        const call = calls.get(result.toolCallId);
        completed.push({
          toolCallId: result.toolCallId,
          toolName: result.toolName ?? call?.toolName ?? 'unknown',
          args: asArgs(call?.input),
          outcome: result.output.type.startsWith('error') ? 'failure' : 'success',
        });
      }
    }
  }
  return completed;
}

async function captureToolTraces(
  coreUrl: string,
  headers: Record<string, string>,
  ctx: Ctx,
  prompt: LanguageModelV2Prompt,
  state: CaptureState,
  sync: boolean,
): Promise<void> {
  for (const tool of collectCompletedTools(prompt)) {
    if (state.seen.has(tool.toolCallId)) continue;
    state.seen.add(tool.toolCallId);
    try {
      const res = await fetch(`${coreUrl}/v1/step`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          tool_name: tool.toolName,
          arguments: tool.args,
          outcome: tool.outcome,
          ctx: { ...ctx, access_level: 'write' },
          prev_step_key: state.lastStepKey,
          sync,
        }),
      });
      if (res.ok) {
        const data = (await res.json()) as { step_id?: string };
        state.lastStepKey = data.step_id ?? state.lastStepKey;
      }
    } catch {
      // best-effort: a procedural write failure never breaks the turn (§15)
    }
  }
}

/** Concatenate the assistant's text parts from a completed generation (MA-4). */
function assistantText(content: readonly unknown[]): string {
  return content
    .filter((p): p is { type: 'text'; text: string } =>
      typeof p === 'object' && p !== null && (p as { type?: string }).type === 'text')
    .map((p) => p.text)
    .join('');
}

export function arangoMemory(options: ArangoMemoryOptions): LanguageModelV2Middleware {
  const {
    coreUrl,
    tenantId,
    agentId,
    sessionId,
    mode,
    maxMemoryTokens,
    readAgentIds,
    syncWrites = false,
    retrieveTimeoutMs = 800,
    captureToolTraces: capture = true,
    captureResponses = true,
    apiKey,
  } = options;
  const ctx: Ctx = { tenant_id: tenantId, agent_id: agentId, session_id: sessionId };
  const headers = jsonHeaders(apiKey);
  const state: CaptureState = { seen: new Set() };

  const store = (content: string): void =>
    void storeTurn(coreUrl, headers, ctx, content, syncWrites);

  /** Fire the after-turn writes shared by generate + stream. */
  const afterTurn = (prompt: LanguageModelV2Prompt): void => {
    store(lastUserText(prompt));
    if (capture) void captureToolTraces(coreUrl, headers, ctx, prompt, state, syncWrites);
  };

  return {
    // BEFORE the model call: retrieve + inject. Failures → unchanged params.
    transformParams: async ({ params }) => {
      const query = lastUserText(params.prompt);
      if (!query) return params;
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), retrieveTimeoutMs);
        const res = await fetch(`${coreUrl}/v1/retrieve`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            query,
            ctx: { ...ctx, access_level: 'read', read_agent_ids: readAgentIds },
            opts: { mode, max_memory_tokens: maxMemoryTokens },
          }),
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (!res.ok) return params;
        const data = (await res.json()) as { context?: string };
        return injectContext(params, data.context ?? '');
      } catch {
        return params; // memory-less turn
      }
    },

    // AFTER generateText: store user + assistant turns + capture tools (non-blocking).
    wrapGenerate: async ({ doGenerate, params }) => {
      const result = await doGenerate();
      afterTurn(params.prompt);
      if (captureResponses) {
        const text = assistantText(result.content ?? []);
        if (text) store(`[assistant] ${text.slice(0, MAX_RESPONSE_CHARS)}`);
      }
      return result;
    },

    // AFTER streamText: store user/tools immediately; tap the stream to store the
    // assistant response onFinish (so it never delays time-to-first-token).
    wrapStream: async ({ doStream, params }) => {
      const out = await doStream();
      afterTurn(params.prompt);
      if (!captureResponses) return out;
      let assistant = '';
      const tap = new TransformStream<LanguageModelV2StreamPart, LanguageModelV2StreamPart>({
        transform(part, controller) {
          if (part.type === 'text-delta') assistant += part.delta;
          controller.enqueue(part);
        },
        flush() {
          if (assistant) store(`[assistant] ${assistant.slice(0, MAX_RESPONSE_CHARS)}`);
        },
      });
      return { ...out, stream: out.stream.pipeThrough(tap) };
    },
  };
}

function storeTurn(
  coreUrl: string,
  headers: Record<string, string>,
  ctx: Ctx,
  content: string,
  sync: boolean,
): Promise<void> {
  if (!content) return Promise.resolve();
  return fetch(`${coreUrl}/v1/store`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ content, ctx: { ...ctx, access_level: 'write' }, sync }),
  })
    .then(() => undefined)
    .catch(() => undefined); // durable queue lives in the core (DESIGN.md §15)
}

// ── Standalone handoff helpers (not middleware) ────────────────────────────

/** Shared connection options for the client helpers. */
export interface CoreConnection {
  coreUrl: string;
  apiKey?: string;
}

export interface PrimeOptions extends CoreConnection {
  task: string;
  tenantId: string;
  agentId: string;
  /** Read across these agents (MA-2) — e.g. own + shared crew tiers. */
  readAgentIds?: string[];
  mode?: 'lite' | 'full';
  k?: number;
  maxMemoryTokens?: number;
  include?: { episodic?: boolean; semantic?: boolean; procedural?: boolean };
}

export interface PrimeResult {
  context: string;
  hits: Array<{ text: string; score: number; source: string; agent_id: string }>;
  entities: Array<Record<string, unknown>>;
  steps: Array<Record<string, unknown>>;
  tokens_injected: number;
}

/**
 * Assemble a task briefing for a handoff (MA-3): retrieved history + key entities +
 * prior tool runs, spanning `readAgentIds`. On any fault returns an empty briefing.
 */
export async function prime(opts: PrimeOptions): Promise<PrimeResult> {
  const empty: PrimeResult = { context: '', hits: [], entities: [], steps: [], tokens_injected: 0 };
  try {
    const res = await fetch(`${opts.coreUrl}/v1/prime`, {
      method: 'POST',
      headers: jsonHeaders(opts.apiKey),
      body: JSON.stringify({
        task: opts.task,
        ctx: { tenant_id: opts.tenantId, agent_id: opts.agentId, read_agent_ids: opts.readAgentIds },
        opts: {
          mode: opts.mode,
          k: opts.k,
          max_memory_tokens: opts.maxMemoryTokens,
          include: opts.include,
        },
      }),
    });
    if (!res.ok) return empty;
    return (await res.json()) as PrimeResult;
  } catch {
    return empty;
  }
}

export interface FlushOptions extends CoreConnection {
  tenantId: string;
  agentId: string;
  timeoutMs?: number;
}

export interface FlushResult {
  status: 'flushed' | 'timeout';
  pending?: number;
}

/**
 * Block until this tenant's queued writes have committed and the search view reflects
 * them (MA-1) — the barrier an orchestrator calls between agent stages. On a transport
 * fault returns `{ status: 'timeout' }` so callers branch uniformly.
 */
export async function flush(opts: FlushOptions): Promise<FlushResult> {
  try {
    const res = await fetch(`${opts.coreUrl}/v1/flush`, {
      method: 'POST',
      headers: jsonHeaders(opts.apiKey),
      body: JSON.stringify({
        ctx: { tenant_id: opts.tenantId, agent_id: opts.agentId },
        timeout_ms: opts.timeoutMs,
      }),
    });
    if (!res.ok) return { status: 'timeout' };
    return (await res.json()) as FlushResult;
  } catch {
    return { status: 'timeout' };
  }
}

export default arangoMemory;
