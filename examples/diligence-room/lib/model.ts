/**
 * LLM provider selection for the specialist agents (DR-1). Prefer the Vercel AI Gateway
 * when its key is present; otherwise call Anthropic directly. Model id is overridable via
 * `DILIGENCE_MODEL`. `chooseProvider` is pure so the wiring is unit-testable without a key.
 */

import { anthropic } from "@ai-sdk/anthropic";
import { gateway } from "@ai-sdk/gateway";
import type { LanguageModel } from "ai";

export type Provider = "gateway" | "anthropic";

/** A small, cheap model is plenty for claim extraction. */
const DEFAULT_MODEL = "claude-haiku-4-5";

export function chooseProvider(env: {
  AI_GATEWAY_API_KEY?: string;
  ANTHROPIC_API_KEY?: string;
}): Provider {
  if (env.AI_GATEWAY_API_KEY) return "gateway";
  if (env.ANTHROPIC_API_KEY) return "anthropic";
  // Default to the Gateway; the missing-key error surfaces at call time, not import time.
  return "gateway";
}

/** True when a provider key is configured — so the War Room knows whether it can run live. */
export function hasProviderKey(): boolean {
  return Boolean(process.env.AI_GATEWAY_API_KEY || process.env.ANTHROPIC_API_KEY);
}

/** The configured language model. Throws only when actually invoked without a key. */
export function getModel(): LanguageModel {
  const provider = chooseProvider({
    AI_GATEWAY_API_KEY: process.env.AI_GATEWAY_API_KEY,
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
  });
  const model = process.env.DILIGENCE_MODEL ?? DEFAULT_MODEL;
  return provider === "anthropic" ? anthropic(model) : gateway(`anthropic/${model}`);
}
