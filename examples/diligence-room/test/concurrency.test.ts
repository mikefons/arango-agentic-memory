import { describe, expect, it } from "vitest";
import { mapLimit } from "../lib/concurrency";

describe("mapLimit", () => {
  it("preserves input order regardless of completion order", async () => {
    // Earlier items resolve later, so completion order is reversed — result order must not be.
    const out = await mapLimit([30, 20, 10], 3, async (ms, i) => {
      await new Promise((r) => setTimeout(r, ms));
      return i;
    });
    expect(out).toEqual([0, 1, 2]);
  });

  it("never runs more than `limit` at once", async () => {
    let inFlight = 0;
    let peak = 0;
    await mapLimit(Array.from({ length: 12 }, (_, i) => i), 4, async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 3));
      inFlight -= 1;
    });
    expect(peak).toBeGreaterThan(1); // actually concurrent
    expect(peak).toBeLessThanOrEqual(4); // but bounded
  });

  it("handles an empty list", async () => {
    expect(await mapLimit([], 5, async () => 1)).toEqual([]);
  });
});
