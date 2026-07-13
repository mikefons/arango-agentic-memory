import { describe, expect, it } from "vitest";

import { estimateTokens, keptTokens, toBriefingItems } from "../lib/briefing";
import type { PrimeResult } from "../lib/types";

const P: PrimeResult = {
  context: "…",
  hits: [
    { text: "the cook lied about the vault", score: 0.9, source: "graph", agent_id: "hero-2" },
    { text: "a torch flickers in the crypt", score: 0.5, source: "bm25", agent_id: "guild::query" },
  ],
  entities: [{ name: "Cook", label: "person", summary: "the kitchen hand" }],
  steps: [{ tool_name: "confront", outcome: "success", use_count: 3 }],
  tokens_injected: 42,
};

describe("briefing view model", () => {
  it("flattens a prime result into history/entity/tool items with provenance", () => {
    const items = toBriefingItems(P);
    expect(items.map((i) => i.kind)).toEqual(["history", "history", "entity", "tool"]);
    expect(items[0].agent).toBe("hero-2");
    expect(items.find((i) => i.kind === "entity")!.text).toBe("Cook (person) — the kitchen hand");
    expect(items.find((i) => i.kind === "tool")!.text).toBe("confront → success (used 3x)");
  });

  it("estimates tokens and sums only the kept (non-dropped) items", () => {
    const items = toBriefingItems(P);
    const total = keptTokens(items, new Set());
    expect(total).toBe(items.reduce((s, i) => s + i.tokens, 0));
    const dropped = new Set([items[0].id]);
    expect(keptTokens(items, dropped)).toBe(total - items[0].tokens);
  });

  it("token estimate is never zero", () => {
    expect(estimateTokens("")).toBe(1);
    expect(estimateTokens("a".repeat(40))).toBe(10);
  });
});
