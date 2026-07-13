import { describe, expect, it } from "vitest";

import { caughtCount, criticalPathLength, evidenceChain } from "../lib/accuse";
import { TORCH_BUDGET } from "../lib/expedition";
import type { MemoryGraph } from "../lib/explorer";
import { ACCUSE_THRESHOLD, isExposable, roomOfItem, traitorNpc } from "../lib/world";

const traitor = traitorNpc();

function graphWith(supersededNames: string[]): MemoryGraph {
  return {
    nodes: supersededNames.map((name, i) => ({
      id: `n${i}`, name, label: "claim", invalid_at: "2020-01-01T00:00:00Z",
    })),
    edges: [],
  };
}

describe("traitor accusation (E-4)", () => {
  it("counts only the traitor's superseded lie-subjects as caught", () => {
    const two = graphWith([traitor.claims[0].subject, traitor.claims[1].subject, "Unrelated Node"]);
    expect(caughtCount(traitor, two)).toBe(2);
    expect(caughtCount(traitor, graphWith([]))).toBe(0);
  });

  it("wins only at or above the threshold", () => {
    const subjects = traitor.claims.filter((c) => c.lie).map((c) => c.subject);
    expect(caughtCount(traitor, graphWith(subjects)) >= ACCUSE_THRESHOLD).toBe(true);
    const short = graphWith(subjects.slice(0, ACCUSE_THRESHOLD - 1));
    expect(caughtCount(traitor, short) >= ACCUSE_THRESHOLD).toBe(false);
  });

  it("builds a truth ⇒ lie evidence chain for caught lies", () => {
    const chain = evidenceChain(traitor, graphWith([traitor.claims[0].subject]));
    expect(chain).toHaveLength(1);
    expect(chain[0]).toEqual({ lie: traitor.claims[0].subject, truth: traitor.claims[0].truth });
  });

  it("is unwinnable in a single expedition — critical path exceeds the torch", () => {
    expect(criticalPathLength(traitor)).toBeGreaterThan(TORCH_BUDGET);
  });

  it("every traitor lie has a reachable evidence source", () => {
    for (const c of traitor.claims.filter((c) => c.lie)) {
      const reachable =
        (c.needs?.item && roomOfItem(c.needs.item)) ||
        (c.needs?.heard && true) ||
        !c.needs;
      expect(Boolean(reachable)).toBe(true);
      // sanity: the lie is genuinely gated (has evidence requirements)
      expect(isExposable(c, { inventory: [], heardClaims: [] })).toBe(false);
    }
  });
});
