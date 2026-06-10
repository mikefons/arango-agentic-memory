import { describe, expect, it } from "vitest";
import { buildGraph, matchesRoom } from "../lib/graph";
import type { Entity, EntityDetail } from "../lib/types";

const ROOMS = ["The Gatehouse", "The Cistern"];

describe("matchesRoom", () => {
  it("matches an extracted name against a known room (substring, case-insensitive)", () => {
    expect(matchesRoom("Gatehouse", ROOMS)).toBe(true);
    expect(matchesRoom("the cistern", ROOMS)).toBe(true);
  });
  it("rejects unrelated or too-short names", () => {
    expect(matchesRoom("Veld", ROOMS)).toBe(false);
    expect(matchesRoom("A", ROOMS)).toBe(false);
  });
});

describe("buildGraph", () => {
  const entities: Entity[] = [
    { id: "g", name: "Gatehouse", label: "Concept" },
    { id: "c", name: "Cistern", label: "Concept" },
    { id: "b", name: "Black", label: "Concept" }, // stray lore
  ];
  const list = async () => entities;
  const get = async (id: string): Promise<EntityDetail | null> => {
    if (id === "g") return { entity: entities[0], related: [{ id: "c", name: "Cistern", label: "Concept", relationship: "associated_with" }] };
    if (id === "c") return { entity: entities[1], related: [{ id: "g", name: "Gatehouse", label: "Concept", relationship: "associated_with" }] };
    return { entity: entities[2], related: [] };
  };

  it("classifies rooms vs lore", async () => {
    const g = await buildGraph(list, get, ROOMS);
    const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n.kind]));
    expect(byId).toEqual({ g: "room", c: "room", b: "lore" });
  });

  it("derives a single deduped edge between the two rooms", async () => {
    const g = await buildGraph(list, get, ROOMS);
    expect(g.edges).toHaveLength(1);
    expect([g.edges[0].source, g.edges[0].target].sort()).toEqual(["c", "g"]);
  });

  it("does not expand lore nodes (no edges from 'Black')", async () => {
    const g = await buildGraph(list, get, ROOMS);
    expect(g.edges.every((e) => e.source !== "b" && e.target !== "b")).toBe(true);
  });
});
