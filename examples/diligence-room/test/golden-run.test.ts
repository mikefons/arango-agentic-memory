import { describe, expect, it } from "vitest";
import { detailCount, goldenCoverage, sameSubject } from "../lib/golden-oracle";
import { DEFECTS } from "../lib/fixtures/northwind";
import { GOLDEN_DISPUTES, GOLDEN_MEMO, GOLDEN_STEPS } from "../lib/fixtures/golden/run";

const stepDetail = (name: string) => GOLDEN_STEPS.find((s) => s.name === name)?.detail;

describe("golden run — defect coverage (DR-5a)", () => {
  const cov = goldenCoverage(DEFECTS, GOLDEN_DISPUTES);

  it("surfaces every contradiction-class planted defect (all but the stale one)", () => {
    const contradictionDefects = DEFECTS.filter((d) => d.kind !== "stale");
    expect(cov.covered).toHaveLength(contradictionDefects.length);
    for (const d of contradictionDefects) {
      expect(cov.covered.some((c) => c.defect.id === d.id)).toBe(true);
    }
  });

  it("leaves exactly the stale defect uncovered (belief-only by design)", () => {
    expect(cov.uncovered).toHaveLength(1);
    expect(cov.uncovered[0].kind).toBe("stale");
    expect(cov.uncovered[0].id).toBe("footprint-stale");
  });

  it("matches each defect to a dispute of the same kind", () => {
    for (const { defect, dispute } of cov.covered) {
      expect(dispute.kind).toBe(defect.kind);
    }
  });

  it("does not credit a defect to an unrelated same-kind dispute", () => {
    // NRR and Litigation are both 'contradiction'; each must map to its own subject.
    const nrr = cov.covered.find((c) => c.defect.id === "nrr-contradiction")!;
    const lit = cov.covered.find((c) => c.defect.id === "litigation-contradiction")!;
    expect(nrr.dispute.subject).toMatch(/retention/i);
    expect(lit.dispute.subject).toMatch(/litigation/i);
    expect(sameSubject(nrr.defect, lit.dispute)).toBe(false);
  });
});

describe("golden run — internal consistency", () => {
  it("the redteam step's count equals the number of disputes", () => {
    expect(detailCount(stepDetail("redteam"))).toBe(GOLDEN_DISPUTES.length);
  });

  it("the synthesis step's count equals the number of memo findings", () => {
    expect(detailCount(stepDetail("synthesis"))).toBe(GOLDEN_MEMO.findings.length);
  });

  it("every dispute has a valid confidence and a resolution (winner/loser)", () => {
    for (const d of GOLDEN_DISPUTES) {
      expect(d.confidence).toBeGreaterThan(0);
      expect(d.confidence).toBeLessThanOrEqual(1);
      expect(d.winner).toBeTruthy();
      expect(d.loser).toBeTruthy();
    }
  });

  it("the memo recommends pass and leads with a risk", () => {
    expect(GOLDEN_MEMO.recommendation).toBe("pass");
    expect(GOLDEN_MEMO.findings[0].kind).toBe("risk");
  });
});
