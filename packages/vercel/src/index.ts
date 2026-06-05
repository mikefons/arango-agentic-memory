/**
 * @arango-memory/vercel — thin client middleware for the ArangoDB memory core.
 *
 * Holds NO memory logic. It (1) retrieves context from the Python core and
 * injects it before a turn, (2) durably stores the turn afterward, and (3)
 * captures completed tool calls as procedural memory. Every core/network
 * failure degrades to a working, memory-less turn (DESIGN.md §15, §20).
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
  LanguageModelV2ToolCallPart,
  LanguageModelV2ToolResultPart,
} from '@ai-sdk/provider';

export interface ArangoMemoryOptions {
  /** URL of the Python core service (DESIGN.md §20). */
  coreUrl: string;
  tenantId: string;
  agentId: string;
  sessionId?: string;
  /** Enrichment mode (DESIGN.md §10). Defaults to the core's configured mode. */
  mode?: 'lite' | 'full';
  maxMemoryTokens?: number;
  /** Abort retrieval if the core is slow, so a turn never hangs on memory. */
  retrieveTimeoutMs?: number;
  /** Record completed tool calls as procedural memory (DESIGN.md §11). Default true. */
  captureToolTraces?: boolean;
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
  ctx: Ctx,
  prompt: LanguageModelV2Prompt,
  state: CaptureState,
): Promise<void> {
  for (const tool of collectCompletedTools(prompt)) {
    if (state.seen.has(tool.toolCallId)) continue;
    state.seen.add(tool.toolCallId);
    try {
      const res = await fetch(`${coreUrl}/v1/step`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          tool_name: tool.toolName,
          arguments: tool.args,
          outcome: tool.outcome,
          ctx: { ...ctx, access_level: 'write' },
          prev_step_key: state.lastStepKey,
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

export function arangoMemory(options: ArangoMemoryOptions): LanguageModelV2Middleware {
  const {
    coreUrl,
    tenantId,
    agentId,
    sessionId,
    mode,
    maxMemoryTokens,
    retrieveTimeoutMs = 800,
    captureToolTraces: capture = true,
  } = options;
  const ctx: Ctx = { tenant_id: tenantId, agent_id: agentId, session_id: sessionId };
  const state: CaptureState = { seen: new Set() };

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
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            query,
            ctx: { ...ctx, access_level: 'read' },
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

    // AFTER generateText: store the turn + capture completed tools (non-blocking).
    wrapGenerate: async ({ doGenerate, params }) => {
      const result = await doGenerate();
      void storeTurn(coreUrl, ctx, lastUserText(params.prompt));
      if (capture) void captureToolTraces(coreUrl, ctx, params.prompt, state);
      return result;
    },

    // AFTER streamText: same, once the stream is handed off.
    wrapStream: async ({ doStream, params }) => {
      const out = await doStream();
      void storeTurn(coreUrl, ctx, lastUserText(params.prompt));
      if (capture) void captureToolTraces(coreUrl, ctx, params.prompt, state);
      return out;
    },
  };
}

function storeTurn(coreUrl: string, ctx: Ctx, content: string): Promise<void> {
  if (!content) return Promise.resolve();
  return fetch(`${coreUrl}/v1/store`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content, ctx: { ...ctx, access_level: 'write' } }),
  })
    .then(() => undefined)
    .catch(() => undefined); // durable queue lives in the core (DESIGN.md §15)
}

export default arangoMemory;
