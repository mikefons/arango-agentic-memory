import { describe, expect, it } from "vitest";
import { runCampaign } from "../lib/campaign";
import type { CampaignDeps } from "../lib/campaign";
import { specialists } from "../lib/agents/specialists";
import type { Memo } from "../lib/agents/synthesis";

const MEMO: Memo = {
  target: "Northwind Robotics",
  recommendation: "pass",
  thesis: "Numbers overstated.",
  findings: [{ title: "ARR", kind: "risk", detail: "d", evidence: ["e"], confidence: 0.9 }],
};

/** A deps double that records the order phases run in. */
function tracer(overrides: Partial<CampaignDeps> = {}) {
  const order: string[] = [];
  const deps: CampaignDeps = {
    runSpecialist: async (c) => {
      order.push(`specialist:${c.id}`);
      return { claimsWritten: 3 };
    },
    flush: async () => void order.push("flush"),
    consolidate: async () => void order.push("consolidate"),
    runRedTeam: async () => {
      order.push("redteam");
      return { disputes: [{}, {}] };
    },
    runSynthesis: async () => {
      order.push("synthesis");
      return MEMO;
    },
    ...overrides,
  };
  return { deps, order };
}

describe("runCampaign", () => {
  it("runs the phases in order: specialists → flush → consolidate → redteam → flush → synthesis", async () => {
    const { deps, order } = tracer();
    const result = await runCampaign({ roomId: "acme", specialists: specialists() }, deps);

    // All four specialists ran first, before the first flush.
    const firstFlush = order.indexOf("flush");
    const specialistPhases = order.slice(0, firstFlush);
    expect(specialistPhases).toEqual(
      specialists().map((s) => `specialist:${s.id}`),
    );
    // Canonical order after dispatch.
    expect(order.slice(firstFlush)).toEqual([
      "flush",
      "consolidate",
      "redteam",
      "flush",
      "synthesis",
    ]);
    expect(result.memo).toEqual(MEMO);
    expect(result.steps.every((s) => s.status === "ok")).toBe(true);
  });

  it("records a failed specialist as an error step but still finishes the campaign", async () => {
    const { deps } = tracer({
      runSpecialist: async (c) => {
        if (c.id === "legal") throw new Error("boom");
        return { claimsWritten: 1 };
      },
    });
    const result = await runCampaign({ roomId: "acme", specialists: specialists() }, deps);
    const legal = result.steps.find((s) => s.name === "specialist:legal");
    expect(legal?.status).toBe("error");
    // The campaign still reaches synthesis (a specialist failure isn't fatal).
    expect(result.memo).toEqual(MEMO);
  });

  it("skips synthesis when the red-team fails (no memo without cross-examination)", async () => {
    const { deps } = tracer({
      runRedTeam: async () => {
        throw new Error("redteam down");
      },
    });
    const result = await runCampaign({ roomId: "acme", specialists: specialists() }, deps);
    expect(result.steps.find((s) => s.name === "redteam")?.status).toBe("error");
    expect(result.steps.find((s) => s.name === "synthesis")?.status).toBe("skipped");
    expect(result.memo).toBeUndefined();
  });
});
