/**
 * Timing and token stats for one LLM call, and the one-line log format the campaign steps use
 * (DR-7 Step 0). Pure, so the formatting is unit-tested; the calls report through an optional
 * callback and nothing about a call's result changes.
 */

export interface LlmCallStats {
  /** Wall-clock time of the call. */
  ms: number;
  inputTokens?: number;
  outputTokens?: number;
  finishReason?: string;
}

export type OnLlmCall = (stats: LlmCallStats) => void;

/** Build stats from a `generateObject` result (usage fields can be undefined per provider). */
export function llmStats(
  ms: number,
  result: {
    usage?: { inputTokens?: number | undefined; outputTokens?: number | undefined };
    finishReason?: string;
  },
): LlmCallStats {
  return {
    ms,
    inputTokens: result.usage?.inputTokens,
    outputTokens: result.usage?.outputTokens,
    finishReason: result.finishReason,
  };
}

/** `7400ms in=2310 out=850 (115 tok/s) finish=stop` — tok/s is output tokens over call time. */
export function describeLlm(s: LlmCallStats): string {
  const parts = [`${s.ms}ms`];
  if (s.inputTokens !== undefined) parts.push(`in=${s.inputTokens}`);
  if (s.outputTokens !== undefined) {
    const rate = s.ms > 0 ? Math.round(s.outputTokens / (s.ms / 1000)) : 0;
    parts.push(`out=${s.outputTokens} (${rate} tok/s)`);
  }
  if (s.finishReason) parts.push(`finish=${s.finishReason}`);
  return parts.join(" ");
}

/** `[campaign] redteam stages — gather: 5012ms (24 claims) | llm: 7400ms … | record: 410ms` */
export function stageLine(step: string, parts: [label: string, value: string][]): string {
  return `[campaign] ${step} stages — ${parts.map(([k, v]) => `${k}: ${v}`).join(" | ")}`;
}
