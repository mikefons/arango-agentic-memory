/**
 * @arango-memory/vercel — thin client middleware for the ArangoDB memory core.
 *
 * Holds NO memory logic. It (1) retrieves context from the Python core and
 * injects it before a turn, and (2) durably stores the turn afterward. Every
 * core/network failure degrades to a working, memory-less turn (DESIGN.md §15).
 *
 * Step 0: real HTTP calls against the core's /v1/* contract. The store path is
 * best-effort fire-and-forget here; the durable queue lands in a later step.
 */

import type {
  LanguageModelV2CallOptions,
  LanguageModelV2Middleware,
  LanguageModelV2Prompt,
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

export function arangoMemory(options: ArangoMemoryOptions): LanguageModelV2Middleware {
  const {
    coreUrl,
    tenantId,
    agentId,
    sessionId,
    mode,
    maxMemoryTokens,
    retrieveTimeoutMs = 800,
  } = options;
  const ctx = { tenant_id: tenantId, agent_id: agentId, session_id: sessionId };

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

    // AFTER generateText: store the turn input (best-effort, non-blocking).
    wrapGenerate: async ({ doGenerate, params }) => {
      const result = await doGenerate();
      void storeTurn(coreUrl, ctx, lastUserText(params.prompt));
      return result;
    },

    // AFTER streamText: store after the stream is handed off.
    wrapStream: async ({ doStream, params }) => {
      const out = await doStream();
      void storeTurn(coreUrl, ctx, lastUserText(params.prompt));
      return out;
    },
  };
}

function storeTurn(
  coreUrl: string,
  ctx: { tenant_id: string; agent_id: string; session_id?: string },
  content: string,
): Promise<void> {
  if (!content) return Promise.resolve();
  return fetch(`${coreUrl}/v1/store`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content, ctx: { ...ctx, access_level: 'write' } }),
  })
    .then(() => undefined)
    .catch(() => undefined); // TODO(durability): replace with durable queue (DESIGN.md §15)
}

export default arangoMemory;
