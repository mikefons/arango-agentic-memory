/**
 * Resolve the DM language model (Showcase polish — direct-provider fallback).
 *
 * Prefer the Vercel AI Gateway (one key, routing/observability). If no Gateway
 * key is set but an Anthropic key is, fall back to calling Anthropic directly —
 * so the dungeon is play-testable without a Gateway account. With neither key we
 * still return a Gateway model; the resulting auth error surfaces in the UI.
 */

import { gateway } from "@ai-sdk/gateway";
import { createAnthropic } from "@ai-sdk/anthropic";

type Env = Record<string, string | undefined>;

export type Provider = "gateway" | "anthropic";

/** Pure provider selection (env-driven) — unit-tested without instantiating SDKs. */
export function chooseProvider(env: Env = process.env): Provider {
  if (env.AI_GATEWAY_API_KEY) return "gateway";
  if (env.ANTHROPIC_API_KEY) return "anthropic";
  return "gateway"; // default; the missing-key error is surfaced in the chat UI
}

export function resolveModel(env: Env = process.env) {
  if (chooseProvider(env) === "anthropic") {
    const anthropic = createAnthropic({ apiKey: env.ANTHROPIC_API_KEY });
    return anthropic(env.DUNGEON_MODEL_DIRECT ?? "claude-sonnet-4-5");
  }
  return gateway(env.DUNGEON_MODEL ?? "anthropic/claude-sonnet-4.5");
}
