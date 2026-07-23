import { describe, expect, it } from "vitest";
import { RECOMMENDATION, findingTally, memoFilename, memoToMarkdown } from "../lib/memo-export";
import { GOLDEN_MEMO } from "../lib/fixtures/golden/run";
import type { Memo } from "../lib/agents/synthesis";

describe("findingTally", () => {
  it("counts risks and strengths", () => {
    const t = findingTally(GOLDEN_MEMO);
    expect(t.risks).toBe(5);
    expect(t.strengths).toBe(1);
    expect(t.risks + t.strengths).toBe(GOLDEN_MEMO.findings.length);
  });
});

describe("RECOMMENDATION", () => {
  it("maps every recommendation to a label + tone", () => {
    expect(RECOMMENDATION.pass.tone).toBe("down");
    expect(RECOMMENDATION.proceed.tone).toBe("ok");
    expect(RECOMMENDATION.proceed_with_conditions.tone).toBe("warn");
  });
});

describe("memoFilename", () => {
  it("slugifies the target", () => {
    expect(memoFilename(GOLDEN_MEMO)).toBe("memo-northwind-robotics.md");
  });

  it("falls back when the target has no slug-able characters", () => {
    const m: Memo = { ...GOLDEN_MEMO, target: "!!!" };
    expect(memoFilename(m)).toBe("memo-target.md");
  });
});

describe("memoToMarkdown", () => {
  const md = memoToMarkdown(GOLDEN_MEMO);

  it("has the title, recommendation, and thesis", () => {
    expect(md).toContain("# Investment Memo — Northwind Robotics");
    expect(md).toContain("**Recommendation:** Pass");
    expect(md).toContain(GOLDEN_MEMO.thesis);
  });

  it("renders Risks before Strengths", () => {
    const risks = md.indexOf("## Risks");
    const strengths = md.indexOf("## Strengths");
    expect(risks).toBeGreaterThan(-1);
    expect(strengths).toBeGreaterThan(risks);
  });

  it("includes every finding title and every evidence line", () => {
    for (const f of GOLDEN_MEMO.findings) {
      expect(md).toContain(f.title);
      for (const e of f.evidence) expect(md).toContain(`- ${e}`);
    }
  });

  it("omits an empty section", () => {
    const m: Memo = { ...GOLDEN_MEMO, findings: GOLDEN_MEMO.findings.filter((f) => f.kind === "risk") };
    expect(memoToMarkdown(m)).not.toContain("## Strengths");
  });
});
