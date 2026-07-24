import { describe, expect, it } from "vitest";
import { narrate } from "../lib/narration";
import { initialWarState, reduceCampaign, PHASES, type WarState } from "../lib/campaign-state";
import { goldenEvents } from "../lib/campaign-stream";
import { GOLDEN_RUN } from "../lib/fixtures/golden/run";

describe("narrate — idle", () => {
  it("invites the viewer to start, step 0", () => {
    const n = narrate(initialWarState());
    expect(n.step).toBe(0);
    expect(n.total).toBe(PHASES.length);
    expect(n.line).toMatch(/press run/i);
    expect(n.agent).toBeNull();
  });
});

describe("narrate — running", () => {
  it("describes the running phase and its memory capability", () => {
    let s = initialWarState();
    s = reduceCampaign(s, { type: "step", step: { name: "specialist:financial", status: "ok", detail: "5 claim(s)" } });
    // financial done → legal is the running cursor
    const n = narrate(s);
    expect(n.agent).toBe("legal");
    expect(n.line).toMatch(/legal/i);
    expect(n.note.length).toBeGreaterThan(0);
    expect(n.step).toBe(1); // one phase done
  });

  it("tints to the red-team when it is cross-examining", () => {
    let s = initialWarState();
    for (const name of ["specialist:financial", "specialist:legal", "specialist:technical", "specialist:market", "flush:specialists", "consolidate"] as const) {
      s = reduceCampaign(s, { type: "step", step: { name, status: "ok" } });
    }
    const n = narrate(s);
    expect(n.agent).toBe("redteam");
    expect(n.line).toMatch(/red-team/i);
  });
});

describe("narrate — done", () => {
  it("reports the verdict and dispute count", () => {
    const s: WarState = goldenEvents(GOLDEN_RUN).reduce(reduceCampaign, initialWarState());
    const n = narrate(s);
    expect(n.step).toBe(n.total);
    expect(n.line).toMatch(/verdict: pass/i);
    expect(n.line).toContain("6 contradictions");
  });
});

describe("narrate — error", () => {
  it("reports an early stop", () => {
    let s = initialWarState();
    s = reduceCampaign(s, { type: "done", ok: false });
    expect(narrate(s).line).toMatch(/stopped early/i);
  });
});
