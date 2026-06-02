/**
 * @arango-memory/vercel — thin client middleware for the ArangoDB memory core.
 *
 * Holds NO memory logic. It (1) retrieves context from the Python core and
 * injects it before a turn, and (2) durably stores the turn afterward. Every
 * core/network failure degrades to a working, memory-less turn (DESIGN.md §15).
 *
 * Step 0 scaffold: defines the public API and middleware shape. Retrieval
 * injection and durable store are wired against the core's /v1/* contract.
 */

import type { LanguageModelV2Middleware } from 'ai';

export interface ArangoMemoryOptions {
  /** URL of the Python core service (DESIGN.md §20). */
  coreUrl: string;
  tenantId: string;
  agentId: string;
  sessionId?: string;
  /** Enrichment mode (DESIGN.md §10). Defaults to the core's configured mode. */
  mode?: 'lite' | 'full';
  maxMemoryTokens?: number;
}

/**
 * Build the memory middleware. Wrap a model:
 *
 * ```ts
 * const model = wrapLanguageModel({
 *   model: anthropic('claude-sonnet-4-6'),
 *   middleware: arangoMemory({ coreUrl, tenantId, agentId }),
 * });
 * ```
 */
export function arangoMemory(options: ArangoMemoryOptions): LanguageModelV2Middleware {
  return {
    // BEFORE the model call: retrieve + inject (DESIGN.md §20).
    transformParams: async ({ params }) => {
      // TODO(step-0): POST {coreUrl}/v1/retrieve, inject context into system prompt.
      // On failure: return params unchanged (memory-less turn).
      return params;
    },

    // AFTER generateText: enqueue a durable store, return immediately.
    wrapGenerate: async ({ doGenerate }) => {
      const result = await doGenerate();
      // TODO(step-0): fire durable POST {coreUrl}/v1/store (non-blocking).
      return result;
    },

    // AFTER streamText: same durable store after stream completes.
    wrapStream: async ({ doStream }) => {
      // TODO(step-0): capture stream + durable store on flush.
      return doStream();
    },
  };
}

export default arangoMemory;
