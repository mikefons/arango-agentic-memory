import { describe, expect, it } from "vitest";
import { PANEL_CAPTIONS, WHY_SHARED_MEMORY } from "../lib/callouts";

describe("WHY_SHARED_MEMORY", () => {
  it("lists the memory capabilities, each with a title and a blurb", () => {
    expect(WHY_SHARED_MEMORY.length).toBeGreaterThanOrEqual(3);
    for (const c of WHY_SHARED_MEMORY) {
      expect(c.title.length).toBeGreaterThan(0);
      expect(c.blurb.length).toBeGreaterThan(20);
    }
  });

  it("covers the differentiators the run exercises", () => {
    const blob = WHY_SHARED_MEMORY.map((c) => c.title).join(" ").toLowerCase();
    expect(blob).toContain("shared");
    expect(blob).toContain("supersession");
    expect(blob).toContain("belief");
    expect(blob).toContain("provenance");
  });
});

describe("PANEL_CAPTIONS", () => {
  it("has a caption for each pinned panel", () => {
    expect(PANEL_CAPTIONS.rail.length).toBeGreaterThan(0);
    expect(PANEL_CAPTIONS.feed.length).toBeGreaterThan(0);
    expect(PANEL_CAPTIONS.graph.length).toBeGreaterThan(0);
  });
});
