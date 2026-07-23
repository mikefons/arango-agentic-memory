import { describe, expect, it } from "vitest";
import { runSpecialist } from "../lib/agents/specialist";
import { specialist, specialists } from "../lib/agents/specialists";
import { chooseProvider } from "../lib/model";
import { claimFromDoc } from "../lib/claims";
import type { ExtractFn, StoreClaimFn, Triple } from "../lib/agents/types";

describe("chooseProvider", () => {
  it("prefers the Gateway when its key is present, else Anthropic, else defaults", () => {
    expect(chooseProvider({ AI_GATEWAY_API_KEY: "g", ANTHROPIC_API_KEY: "a" })).toBe("gateway");
    expect(chooseProvider({ ANTHROPIC_API_KEY: "a" })).toBe("anthropic");
    expect(chooseProvider({})).toBe("gateway");
  });
});

describe("financial specialist config", () => {
  it("is wired and reads a non-empty slice of the data room", () => {
    const fin = specialist("financial");
    expect(fin).toBeDefined();
    expect(fin!.docs.length).toBeGreaterThan(0);
    expect(fin!.mandate.toLowerCase()).toContain("revenue");
    expect(specialists().map((s) => s.id)).toContain("financial");
  });

  it("returns undefined for a specialist not yet wired (DR-1b)", () => {
    expect(specialist("legal")).toBeUndefined();
  });
});

describe("runSpecialist (orchestration, LLM + core injected)", () => {
  it("extracts per document and stores every claim under the specialist's agent id", async () => {
    const fin = specialist("financial")!;
    // Deterministic fake extractor: one triple per doc, echoing its id so we can assert.
    const extract: ExtractFn = async (doc): Promise<Triple[]> => [
      { subject: `${doc.id}-subject`, predicate: "reported", value: "x" },
    ];

    const stored: { agent: string; docId: string; reliability: number }[] = [];
    const store: StoreClaimFn = async (agentId, doc, triple) => {
      // Mirror the route's provenance wiring so the test covers claimFromDoc too.
      const claim = claimFromDoc(doc, triple);
      stored.push({ agent: agentId, docId: doc.id, reliability: claim.source_reliability });
    };

    const run = await runSpecialist(fin, { extract, store });

    expect(run.specialist).toBe("financial");
    expect(run.docsRead).toBe(fin.docs.length);
    expect(run.claimsWritten).toBe(fin.docs.length); // one triple per doc
    // Every write is under the financial agent, and carries the source doc's reliability prior.
    expect(stored.every((s) => s.agent === "financial")).toBe(true);
    expect(stored.every((s) => s.reliability > 0 && s.reliability <= 1)).toBe(true);
    // The audited filing's claim outranks the pitch deck's for trust.
    const filing = stored.find((s) => s.docId === "filing-q4");
    const deck = stored.find((s) => s.docId === "deck-financials");
    expect(filing && deck && filing.reliability > deck.reliability).toBe(true);
  });

  it("writes nothing when extraction yields no triples", async () => {
    const fin = specialist("financial")!;
    const extract: ExtractFn = async () => [];
    let writes = 0;
    const store: StoreClaimFn = async () => {
      writes += 1;
    };
    const run = await runSpecialist(fin, { extract, store });
    expect(run.claimsWritten).toBe(0);
    expect(writes).toBe(0);
  });
});
