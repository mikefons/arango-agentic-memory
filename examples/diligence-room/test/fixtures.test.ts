import { describe, expect, it } from "vitest";
import {
  DATA_ROOM,
  DEFECTS,
  RELIABILITY,
  docsForSpecialist,
  sourceReliability,
} from "../lib/fixtures";
import type { DefectKind } from "../lib/fixtures";

describe("data room", () => {
  it("has enough documents with unique ids", () => {
    expect(DATA_ROOM.length).toBeGreaterThanOrEqual(15);
    const ids = DATA_ROOM.map((d) => d.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every document is well-formed (date parses, reliability from its type, has readers/text)", () => {
    for (const d of DATA_ROOM) {
      expect(Number.isNaN(Date.parse(d.as_of)), `${d.id} as_of`).toBe(false);
      expect(d.reliability).toBe(sourceReliability(d.type));
      expect(d.reliability).toBeGreaterThanOrEqual(0);
      expect(d.reliability).toBeLessThanOrEqual(1);
      expect(d.forSpecialists.length, `${d.id} readers`).toBeGreaterThan(0);
      expect(d.text.length, `${d.id} text`).toBeGreaterThan(40);
    }
  });

  it("gives every specialist something to read", () => {
    for (const s of ["financial", "legal", "technical", "market"] as const) {
      expect(docsForSpecialist(s).length, s).toBeGreaterThan(0);
    }
  });
});

describe("reliability priors", () => {
  it("rank filing > news > management claim > blog (the analyst's rule of thumb)", () => {
    expect(RELIABILITY.audited_filing).toBeGreaterThan(RELIABILITY.news);
    expect(RELIABILITY.news).toBeGreaterThan(RELIABILITY.management_qa);
    expect(RELIABILITY.management_qa).toBeGreaterThan(RELIABILITY.blog);
    expect(RELIABILITY.signed_contract).toBeGreaterThan(RELIABILITY.blog);
    expect(RELIABILITY.court_record).toBeGreaterThan(RELIABILITY.management_qa);
  });
});

describe("planted defects (the acceptance oracle)", () => {
  it("plants at least 5 defects spanning every kind", () => {
    expect(DEFECTS.length).toBeGreaterThanOrEqual(5);
    const kinds = new Set<DefectKind>(DEFECTS.map((d) => d.kind));
    for (const k of [
      "temporal_drift",
      "contradiction",
      "reliability",
      "related_party",
      "stale",
    ] as const) {
      expect(kinds.has(k), `missing defect kind: ${k}`).toBe(true);
    }
  });

  it("every defect references real documents and is fully specified", () => {
    const ids = new Set(DATA_ROOM.map((d) => d.id));
    const defectIds = DEFECTS.map((d) => d.id);
    expect(new Set(defectIds).size).toBe(defectIds.length);
    for (const def of DEFECTS) {
      expect(def.docs.length, `${def.id} docs`).toBeGreaterThan(0);
      for (const docId of def.docs) {
        expect(ids.has(docId), `${def.id} → ${docId}`).toBe(true);
      }
      expect(def.subject.length, `${def.id} subject`).toBeGreaterThan(0);
      expect(def.resolution.length, `${def.id} resolution`).toBeGreaterThan(0);
    }
  });

  it("the related-party defect actually spans customer, investor, and CFO documents", () => {
    const rp = DEFECTS.find((d) => d.kind === "related_party");
    expect(rp).toBeDefined();
    // cap-table (investor↔customer) + org-chart (CFO↔investor) must both be in evidence.
    expect(rp?.docs).toContain("cap-table");
    expect(rp?.docs).toContain("org-chart");
  });
});
