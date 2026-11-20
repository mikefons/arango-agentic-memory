import { describe, expect, it } from "vitest";
import { MockLanguageModelV2 } from "ai/test";
import { describeLlm, llmStats, stageLine, type LlmCallStats } from "../lib/agents/llm-stats";
import { makeDisputeFinder } from "../lib/agents/redteam";
import { makeMemoWriter } from "../lib/agents/synthesis";

describe("describeLlm", () => {
  it("reports time, tokens, generation speed and the finish reason", () => {
    expect(
      describeLlm({ ms: 8000, inputTokens: 2310, outputTokens: 800, finishReason: "stop" }),
    ).toBe("8000ms in=2310 out=800 (100 tok/s) finish=stop");
  });

  it("leaves out what the provider did not report", () => {
    expect(describeLlm({ ms: 1200 })).toBe("1200ms");
    expect(describeLlm({ ms: 0, outputTokens: 5 })).toBe("0ms out=5 (0 tok/s)");
  });
});

describe("llmStats", () => {
  it("copies usage and finish reason from a generateObject result", () => {
    expect(
      llmStats(500, { usage: { inputTokens: 10, outputTokens: 4 }, finishReason: "length" }),
    ).toEqual({ ms: 500, inputTokens: 10, outputTokens: 4, finishReason: "length" });
  });
});

describe("stageLine", () => {
  it("renders one greppable line per step", () => {
    expect(
      stageLine("redteam", [
        ["gather", "5012ms (24 claims)"],
        ["llm", "7400ms"],
      ]),
    ).toBe("[campaign] redteam stages — gather: 5012ms (24 claims) | llm: 7400ms");
  });
});

function mockModel(json: unknown) {
  return new MockLanguageModelV2({
    doGenerate: async () => ({
      content: [{ type: "text", text: JSON.stringify(json) }],
      finishReason: "stop",
      usage: { inputTokens: 120, outputTokens: 30, totalTokens: 150 },
      warnings: [],
    }),
  });
}

describe("the model calls report their stats without changing what they return", () => {
  it("dispute finder", async () => {
    const calls: LlmCallStats[] = [];
    const find = makeDisputeFinder(mockModel({ disputes: [] }), (s) => calls.push(s));
    expect(await find([{ text: "ARR is $5M", agent_id: "financial" }])).toEqual([]);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ inputTokens: 120, outputTokens: 30, finishReason: "stop" });
  });

  it("memo writer", async () => {
    const calls: LlmCallStats[] = [];
    const memo = { target: "Northwind", recommendation: "pass", thesis: "t", findings: [] };
    const write = makeMemoWriter(mockModel(memo), (s) => calls.push(s));
    expect(await write("briefing")).toEqual(memo);
    expect(calls).toHaveLength(1);
    expect(calls[0].ms).toBeGreaterThanOrEqual(0);
    expect(calls[0]).toMatchObject({ inputTokens: 120, outputTokens: 30 });
  });
});
