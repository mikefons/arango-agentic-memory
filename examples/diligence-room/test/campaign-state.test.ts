import { describe, expect, it } from "vitest";
import {
  PHASES,
  initialWarState,
  phaseAgent,
  reduceCampaign,
  type WarState,
} from "../lib/campaign-state";
import { goldenEvents } from "../lib/campaign-stream";
import { GOLDEN_RUN } from "../lib/fixtures/golden/run";

function play(events = goldenEvents(GOLDEN_RUN)): WarState {
  return events.reduce(reduceCampaign, initialWarState());
}

describe("phaseAgent", () => {
  it("maps specialist/redteam/synthesis phases to agents; flush/consolidate to none", () => {
    expect(phaseAgent("specialist:financial")).toBe("financial");
    expect(phaseAgent("redteam")).toBe("redteam");
    expect(phaseAgent("synthesis")).toBe("synthesis");
    expect(phaseAgent("flush:specialists")).toBeNull();
    expect(phaseAgent("consolidate")).toBeNull();
  });
});

describe("reduceCampaign — initial", () => {
  it("starts idle with every phase pending and every agent idle", () => {
    const s = initialWarState();
    expect(s.run).toBe("idle");
    expect(Object.values(s.phases).every((p) => p === "pending")).toBe(true);
    expect(Object.values(s.agents).every((a) => a.status === "idle" && a.count === 0)).toBe(true);
  });
});

describe("reduceCampaign — live cursor", () => {
  it("marks the first phase done and the next phase running", () => {
    let s = initialWarState();
    s = reduceCampaign(s, { type: "step", step: { name: "specialist:financial", status: "ok", detail: "5 claim(s)" } });
    expect(s.run).toBe("running");
    expect(s.phases["specialist:financial"]).toBe("done");
    expect(s.phases["specialist:legal"]).toBe("running"); // the cursor
    expect(s.agents.financial).toEqual({ status: "done", count: 5 });
    expect(s.agents.legal.status).toBe("running");
  });
});

describe("reduceCampaign — full golden run", () => {
  it("ends done, every phase done, and populates disputes + memo + counts", () => {
    const s = play();
    expect(s.run).toBe("done");
    expect(PHASES.every((p) => s.phases[p] === "done")).toBe(true);
    expect(s.disputes).toHaveLength(6);
    expect(s.memo?.recommendation).toBe("pass");
    expect(s.agents.redteam.count).toBe(6);
    expect(s.agents.synthesis).toEqual({ status: "done", count: GOLDEN_RUN.memo.findings.length });
    expect(s.agents.financial.status).toBe("done");
  });
});

describe("reduceCampaign — failure", () => {
  it("reports error status when done reports failure", () => {
    let s = initialWarState();
    s = reduceCampaign(s, { type: "done", ok: false });
    expect(s.run).toBe("error");
  });
});
