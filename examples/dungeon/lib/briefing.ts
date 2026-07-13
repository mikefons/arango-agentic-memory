/**
 * Handoff-briefing view model (GUILD.md E-2). Turns a `prime()` result into flat,
 * pin/droppable items and computes the token budget the player is spending — the
 * `max_memory_tokens` tradeoff made tangible. Pure + unit-testable.
 */

import type { PrimeResult } from "./types";

export type BriefingKind = "history" | "entity" | "tool";

export interface BriefingItem {
  id: string;
  kind: BriefingKind;
  text: string;
  tokens: number;
  agent?: string; // provenance (MA-2): whose memory this came from
}

/** Rough client-side token estimate (~4 chars/token) — the bar is a visualization. */
export function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : v == null ? fallback : String(v);
}

export function toBriefingItems(p: PrimeResult): BriefingItem[] {
  const items: BriefingItem[] = [];
  p.hits.forEach((h, i) =>
    items.push({ id: `h${i}`, kind: "history", text: h.text, tokens: estimateTokens(h.text), agent: h.agent_id }),
  );
  p.entities.forEach((e, i) => {
    const name = str(e.name, "?");
    const label = e.label ? ` (${str(e.label)})` : "";
    const summary = e.summary ? ` — ${str(e.summary)}` : "";
    const text = `${name}${label}${summary}`;
    items.push({ id: `e${i}`, kind: "entity", text, tokens: estimateTokens(text) });
  });
  p.steps.forEach((s, i) => {
    const used = typeof s.use_count === "number" && s.use_count > 1 ? ` (used ${s.use_count}x)` : "";
    const text = `${str(s.tool_name, "?")} → ${str(s.outcome, "?")}${used}`;
    items.push({ id: `s${i}`, kind: "tool", text, tokens: estimateTokens(text) });
  });
  return items;
}

/** Tokens the player is keeping (everything not dropped). */
export function keptTokens(items: BriefingItem[], dropped: ReadonlySet<string>): number {
  return items.reduce((sum, it) => (dropped.has(it.id) ? sum : sum + it.tokens), 0);
}
