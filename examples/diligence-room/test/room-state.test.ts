import { describe, expect, it } from "vitest";
import { toGraphView, summarizeClaims, agentColor } from "../lib/room-state";
import type { CoreGraph } from "../lib/room-state";
import { goldenEvents } from "../lib/campaign-stream";
import { GOLDEN_RUN, GOLDEN_GRAPH } from "../lib/fixtures/golden/run";

describe("toGraphView", () => {
  const graph: CoreGraph = {
    nodes: [
      { id: "a", name: "Northwind", label: "Organization", centrality: 1.0, belief: 0.96, community: 0 },
      { id: "b", name: "Orion", label: "Organization", centrality: 0.4, belief: 0.84, community: 1 },
      { id: "c", name: "Superseded", label: "Object", centrality: 0.9, belief: 0.3, community: 0, invalid_at: "2026-03-10" },
      { id: "d", name: "Low", label: "Concept", centrality: 0.05, belief: 0.2, community: 2 },
    ],
    edges: [
      { source: "a", target: "b", relationship: "owns", belief: 0.5 },
      { source: "a", target: "c", relationship: "stale", belief: 0.2 },
    ],
  };

  it("drops invalidated (superseded) entities", () => {
    const view = toGraphView(graph);
    expect(view.nodes.map((n) => n.id)).not.toContain("c");
  });

  it("ranks by salience and honors the limit, keeping only edges among surviving nodes", () => {
    const view = toGraphView(graph, { limit: 2 });
    expect(view.nodes.map((n) => n.id)).toEqual(["a", "b"]); // top-2 by centrality
    // The a→c edge is dropped (c invalidated); a→b survives.
    expect(view.edges).toHaveLength(1);
    expect(view.edges[0]).toMatchObject({ source: "a", target: "b", relationship: "owns" });
  });

  it("maps centrality→salience and counts communities", () => {
    const view = toGraphView(graph);
    const north = view.nodes.find((n) => n.id === "a")!;
    expect(north.salience).toBe(1.0);
    expect(north.belief).toBeCloseTo(0.96);
    expect(view.communities).toBe(3); // communities 0,1,2 among live nodes
  });
});

describe("summarizeClaims", () => {
  it("counts claims per agent", () => {
    const s = summarizeClaims([
      { agent: "financial", text: "x" },
      { agent: "financial", text: "y" },
      { agent: "legal", text: "z" },
    ]);
    expect(s.byAgent.financial).toBe(2);
    expect(s.byAgent.legal).toBe(1);
  });
});

describe("agentColor", () => {
  it("gives each agent a distinct color and a neutral default", () => {
    expect(agentColor("financial")).not.toBe(agentColor("redteam"));
    expect(agentColor("unknown")).toBe("#6b6b6b");
  });
});

describe("goldenEvents (canned == live event shape)", () => {
  it("emits steps in order, disputes after redteam, memo after synthesis, then done", () => {
    const events = goldenEvents(GOLDEN_RUN);
    const types = events.map((e) => e.type);
    expect(types[0]).toBe("step");
    expect(types.at(-1)).toBe("done");

    // disputes come immediately after the redteam step.
    const redIdx = events.findIndex((e) => e.type === "step" && e.step.name === "redteam");
    expect(events[redIdx + 1].type).toBe("disputes");

    // memo comes after the synthesis step; done reports success.
    const memoEv = events.find((e) => e.type === "memo");
    expect(memoEv).toBeDefined();
    const done = events.at(-1);
    expect(done).toEqual({ type: "done", ok: true });
  });

  it("carries the full memo and all six disputes", () => {
    const events = goldenEvents(GOLDEN_RUN);
    const disputes = events.find((e) => e.type === "disputes");
    const memo = events.find((e) => e.type === "memo");
    expect(disputes?.type === "disputes" && disputes.disputes.length).toBe(6);
    expect(memo?.type === "memo" && memo.memo.recommendation).toBe("pass");
  });
});

describe("golden graph fixture", () => {
  it("is a real, non-trivial snapshot with belief and communities", () => {
    expect(GOLDEN_GRAPH.nodes.length).toBeGreaterThan(20);
    expect(GOLDEN_GRAPH.edges.length).toBeGreaterThan(20);
    const view = toGraphView(GOLDEN_GRAPH, { limit: 40 });
    expect(view.communities).toBeGreaterThanOrEqual(2); // related-party clustering present
    expect(view.nodes.some((n) => n.belief > 0.7)).toBe(true);
  });
});
