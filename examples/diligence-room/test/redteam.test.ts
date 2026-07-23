import { describe, expect, it } from "vitest";
import { disputeText, runRedTeam } from "../lib/agents/redteam";
import type { ClaimRecord, Dispute, DisputeFinder } from "../lib/agents/redteam";

const CLAIMS: ClaimRecord[] = [
  { agent_id: "financial", text: "Northwind ARR — reported: $8.0M. Source: Northwind management (as of 2026-01-15)." },
  { agent_id: "financial", text: "Northwind ARR — reported: $5.2M. Source: Brayton & Kell LLP (as of 2026-03-10)." },
  { agent_id: "legal", text: "Northwind litigation — no material litigation. Source: Northwind management." },
  { agent_id: "legal", text: "Vertex sued Northwind on 2026-01-28. Source: court records." },
];

describe("disputeText", () => {
  it("renders a self-describing red-team finding with winner/loser", () => {
    const d: Dispute = {
      subject: "Northwind ARR",
      kind: "temporal_drift",
      summary: "The audited March figure supersedes the January deck.",
      winner: "$5.2M",
      loser: "$8.0M",
      confidence: 0.9,
    };
    const text = disputeText(d);
    expect(text).toContain("RED-TEAM (temporal_drift)");
    expect(text).toContain("Northwind ARR");
    expect(text).toContain('Trust "$5.2M" over "$8.0M"');
  });
});

describe("runRedTeam (orchestration, LLM + core injected)", () => {
  it("finds disputes across the pooled claims and records every one", async () => {
    const find: DisputeFinder = async (claims) => {
      // A fake finder that emits the two disputes latent in the CLAIMS pool.
      expect(claims.length).toBe(CLAIMS.length);
      return [
        { subject: "Northwind ARR", kind: "temporal_drift", summary: "audited supersedes deck", winner: "$5.2M", loser: "$8.0M", confidence: 0.9 },
        { subject: "Northwind litigation", kind: "contradiction", summary: "court record refutes 'no litigation'", winner: "Vertex suit", loser: "no litigation", confidence: 0.85 },
      ];
    };

    const recorded: Dispute[] = [];
    const run = await runRedTeam({ claims: CLAIMS, find, record: async (d) => void recorded.push(d) });

    expect(run.claimsReviewed).toBe(CLAIMS.length);
    expect(run.disputes).toHaveLength(2);
    expect(recorded).toHaveLength(2);
    expect(recorded.map((d) => d.kind)).toEqual(["temporal_drift", "contradiction"]);
    // The finding's reliability is its confidence — high-confidence disputes weigh more.
    expect(recorded.every((d) => d.confidence > 0 && d.confidence <= 1)).toBe(true);
  });

  it("records nothing when the finder sees no disputes", async () => {
    let writes = 0;
    const run = await runRedTeam({
      claims: CLAIMS,
      find: async () => [],
      record: async () => void (writes += 1),
    });
    expect(run.disputes).toHaveLength(0);
    expect(writes).toBe(0);
  });
});
