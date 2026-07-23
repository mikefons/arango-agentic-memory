import { describe, expect, it } from "vitest";
import { DISPUTE_KIND, disputeNodeIds, disputeTerms, terms } from "../lib/dispute-map";
import { toGraphView } from "../lib/room-state";
import { GOLDEN_DISPUTES, GOLDEN_GRAPH } from "../lib/fixtures/golden/run";
import type { Dispute } from "../lib/agents/redteam";

const view = toGraphView(GOLDEN_GRAPH, { limit: 24 });
const nameOf = (ids: Set<string>) => view.nodes.filter((n) => ids.has(n.id)).map((n) => n.name);

describe("terms", () => {
  it("lowercases, drops punctuation + short tokens, keeps money/percent tokens", () => {
    const t = terms("$5.2M (audited)");
    expect(t.has("5.2m")).toBe(true);
    expect(t.has("audited")).toBe(true);
  });

  it("drops the target name + function words", () => {
    const t = terms("Northwind Robotics is a");
    expect(t.has("northwind")).toBe(false);
    expect(t.has("robotics")).toBe(false);
    expect(t.size).toBe(0);
  });
});

describe("disputeTerms — source stripping", () => {
  it("ignores the parenthetical source so a shared source word doesn't bleed", () => {
    const d: Dispute = {
      subject: "Navigation technology",
      kind: "reliability",
      summary: "…",
      winner: "open-source fork, 97.5% (audit)",
      loser: "proprietary, 99.9% (deck)",
      confidence: 0.8,
    };
    const t = disputeTerms(d);
    expect(t.has("deck")).toBe(false); // came only from "(deck)"
    expect(t.has("proprietary")).toBe(true);
    expect(t.has("navigation")).toBe(true);
  });
});

describe("disputeNodeIds — golden disputes", () => {
  it("lights the ARR cluster for the ARR temporal drift", () => {
    const arr = GOLDEN_DISPUTES.find((d) => d.subject === "Northwind ARR")!;
    const names = nameOf(disputeNodeIds(arr, view.nodes));
    expect(names).toContain("Northwind ARR");
    expect(names).toContain("Deck ARR");
    expect(names).toContain("Audited ARR");
  });

  it("does not bleed the navigation dispute onto the ARR nodes", () => {
    const nav = GOLDEN_DISPUTES.find((d) => d.subject === "Navigation technology")!;
    const names = nameOf(disputeNodeIds(nav, view.nodes));
    expect(names).toContain("Northwind navigation");
    expect(names).not.toContain("Deck ARR");
    expect(names).not.toContain("Audited ARR");
  });

  it("every golden dispute matches at least one visible node", () => {
    for (const d of GOLDEN_DISPUTES) {
      expect(disputeNodeIds(d, view.nodes).size).toBeGreaterThan(0);
    }
  });

  it("returns an empty set when nothing matches", () => {
    const d: Dispute = { subject: "Nonexistent widget XYZ", kind: "stale", summary: "", confidence: 0.5 };
    expect(disputeNodeIds(d, view.nodes).size).toBe(0);
  });
});

describe("DISPUTE_KIND", () => {
  it("has metadata for every dispute kind used in the golden run", () => {
    for (const d of GOLDEN_DISPUTES) {
      expect(DISPUTE_KIND[d.kind]).toBeDefined();
      expect(DISPUTE_KIND[d.kind].label.length).toBeGreaterThan(0);
    }
  });
});
