import { describe, expect, it } from "vitest";
import {
  COMMUNITY_PALETTE,
  beliefBorderAlpha,
  beliefPct,
  communityHue,
  nodeWidth,
} from "../lib/graph-viz";

describe("communityHue", () => {
  it("is stable, distinct per community, and wraps safely", () => {
    expect(communityHue(0)).toBe(COMMUNITY_PALETTE[0]);
    expect(communityHue(1)).not.toBe(communityHue(0));
    // wraps past the palette length without throwing / returning undefined
    expect(communityHue(COMMUNITY_PALETTE.length)).toBe(COMMUNITY_PALETTE[0]);
    expect(communityHue(-1)).toBe(COMMUNITY_PALETTE[COMMUNITY_PALETTE.length - 1]);
  });
});

describe("nodeWidth", () => {
  it("is monotonic in salience and bounded", () => {
    expect(nodeWidth(0)).toBe(132);
    expect(nodeWidth(1)).toBe(240);
    expect(nodeWidth(0.5)).toBeGreaterThan(nodeWidth(0));
    expect(nodeWidth(0.5)).toBeLessThan(nodeWidth(1));
    // clamps out-of-range input
    expect(nodeWidth(2)).toBe(240);
    expect(nodeWidth(-1)).toBe(132);
  });
});

describe("belief mappings", () => {
  it("beliefPct is a clamped 0..100 integer", () => {
    expect(beliefPct(0)).toBe(0);
    expect(beliefPct(0.84)).toBe(84);
    expect(beliefPct(1.5)).toBe(100);
  });
  it("beliefBorderAlpha rises with belief within [0.35,1]", () => {
    expect(beliefBorderAlpha(0)).toBeCloseTo(0.35);
    expect(beliefBorderAlpha(1)).toBeCloseTo(1);
    expect(beliefBorderAlpha(0.5)).toBeGreaterThan(beliefBorderAlpha(0));
  });
});
