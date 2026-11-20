import { describe, expect, it } from "vitest";
import { memoHeadline, runSynthesis } from "../lib/agents/synthesis";
import type { Memo, MemoWriter } from "../lib/agents/synthesis";

const MEMO: Memo = {
  target: "Northwind Robotics",
  recommendation: "proceed_with_conditions",
  thesis: "Promising automation play, but the reported numbers overstate the business.",
  findings: [
    {
      title: "ARR materially overstated",
      kind: "risk",
      detail: "The deck's $8.0M ARR is contradicted by the audited $5.2M.",
      evidence: ["deck: $8.0M (Jan)", "audited filing: $5.2M (Mar)"],
      confidence: 0.9,
    },
    {
      title: "Undisclosed litigation",
      kind: "risk",
      detail: "Management claimed no litigation; court records show Vertex's $1.4M suit.",
      evidence: ["management Q&A", "court record 2026-01-28"],
      confidence: 0.85,
    },
    {
      title: "Real automation demand",
      kind: "strength",
      detail: "Multiple reference customers deploying.",
      evidence: ["customer list"],
      confidence: 0.5,
    },
  ],
};

describe("runSynthesis (memo writer injected)", () => {
  it("fails, and never calls the writer, on an empty briefing", async () => {
    let calls = 0;
    const write = async () => {
      calls += 1;
      throw new Error("should not be called");
    };
    await expect(runSynthesis("   \n", write)).rejects.toThrow(/briefing is empty/);
    expect(calls).toBe(0);
  });

  it("passes the briefing to the writer and returns its memo", async () => {
    let seen = "";
    const write: MemoWriter = async (briefing) => {
      seen = briefing;
      return MEMO;
    };
    const memo = await runSynthesis("BRIEFING TEXT", write);
    expect(seen).toBe("BRIEFING TEXT");
    expect(memo.target).toBe("Northwind Robotics");
    expect(memo.findings).toHaveLength(3);
  });
});

describe("memo shape", () => {
  it("leads with risks and each finding carries an evidence chain", () => {
    // Red flags should be representable and cite evidence.
    const risks = MEMO.findings.filter((f) => f.kind === "risk");
    expect(risks.length).toBeGreaterThanOrEqual(2);
    for (const f of MEMO.findings) {
      expect(f.evidence.length, f.title).toBeGreaterThan(0);
      expect(f.confidence).toBeGreaterThan(0);
      expect(f.confidence).toBeLessThanOrEqual(1);
    }
  });

  it("headline summarizes the verdict and risk count", () => {
    expect(memoHeadline(MEMO)).toBe("Northwind Robotics: proceed with conditions — 2 risk(s) flagged.");
  });
});
