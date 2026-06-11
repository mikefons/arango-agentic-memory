import { describe, expect, it } from "vitest";
import {
  filterGraph,
  isSuperseded,
  relationshipKinds,
  searchMatches,
  type MemoryGraph,
} from "../lib/explorer";

const graph: MemoryGraph = {
  nodes: [
    { id: "a", name: "Alice", label: "Person", invalid_at: null },
    { id: "b", name: "Bob", label: "Person", invalid_at: null },
    { id: "c", name: "Old Tale", label: "Concept", invalid_at: "2026-01-01T00:00:00+00:00" },
  ],
  edges: [
    { source: "a", target: "b", relationship: "associated_with", kind: "relates_to" },
    { source: "b", target: "c", relationship: "supersedes", kind: "supersedes" },
  ],
};

describe("isSuperseded", () => {
  it("flags entities with invalid_at", () => {
    expect(isSuperseded(graph.nodes[2])).toBe(true);
    expect(isSuperseded(graph.nodes[0])).toBe(false);
  });
});

describe("relationshipKinds", () => {
  it("lists the distinct relationship labels", () => {
    expect(relationshipKinds(graph)).toEqual(["associated_with", "supersedes"]);
  });
});

describe("filterGraph", () => {
  const all = new Set(["associated_with", "supersedes"]);

  it("hides superseded nodes and their dangling edges by default", () => {
    const out = filterGraph(graph, { showSuperseded: false, relationships: all });
    expect(out.nodes.map((n) => n.id)).toEqual(["a", "b"]);
    // the b→c (supersedes) edge drops because c is hidden
    expect(out.edges).toHaveLength(1);
    expect(out.edges[0].relationship).toBe("associated_with");
  });

  it("shows superseded nodes + edges when toggled on", () => {
    const out = filterGraph(graph, { showSuperseded: true, relationships: all });
    expect(out.nodes).toHaveLength(3);
    expect(out.edges).toHaveLength(2);
  });

  it("drops edges whose relationship is filtered out", () => {
    const out = filterGraph(graph, {
      showSuperseded: true,
      relationships: new Set(["associated_with"]),
    });
    expect(out.edges.every((e) => e.relationship === "associated_with")).toBe(true);
  });
});

describe("searchMatches", () => {
  it("matches node names case-insensitively", () => {
    expect(searchMatches(graph.nodes, "ali")).toEqual(new Set(["a"]));
    expect(searchMatches(graph.nodes, "")).toEqual(new Set());
  });
});
