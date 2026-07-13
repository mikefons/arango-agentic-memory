import { describe, expect, it } from "vitest";

import {
  firstExpedition,
  heroId,
  nextExpedition,
  spendTorch,
  TORCH_BUDGET,
  torchSpent,
} from "../lib/expedition";

describe("expedition lifecycle", () => {
  it("first expedition is hero-1 with a full torch", () => {
    expect(firstExpedition()).toEqual({ expedition: 1, heroId: "hero-1", torch: TORCH_BUDGET });
  });

  it("heroes are numbered per expedition", () => {
    expect(heroId(3)).toBe("hero-3");
    expect(nextExpedition(1)).toEqual({ expedition: 2, heroId: "hero-2", torch: TORCH_BUDGET });
    expect(nextExpedition(2).heroId).toBe("hero-3");
  });

  it("torch burns down to zero and no further", () => {
    let t = 2;
    t = spendTorch(t);
    expect(t).toBe(1);
    expect(torchSpent(t)).toBe(false);
    t = spendTorch(t);
    expect(t).toBe(0);
    expect(torchSpent(t)).toBe(true);
    expect(spendTorch(0)).toBe(0); // never negative
  });
});
