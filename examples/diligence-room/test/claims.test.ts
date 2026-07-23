import { describe, expect, it } from "vitest";
import { claimFromDoc, claimText } from "../lib/claims";
import { DATA_ROOM } from "../lib/fixtures";

describe("claimText", () => {
  it("is self-describing: subject, value, source, and a parseable as-of date", () => {
    const text = claimText({
      subject: "Northwind Robotics ARR",
      predicate: "reported",
      value: "$5.2M",
      source: "Brayton & Kell LLP",
      source_reliability: 0.95,
      as_of: "2026-03-10",
    });
    expect(text).toContain("Northwind Robotics ARR");
    expect(text).toContain("$5.2M");
    expect(text).toContain("Brayton & Kell LLP");
    expect(text).toContain("as of 2026-03-10");
  });

  it("omits the as-of clause when there is no date", () => {
    const text = claimText({
      subject: "X",
      predicate: "is",
      value: "Y",
      source: "S",
      source_reliability: 0.5,
    });
    expect(text).not.toContain("as of");
  });
});

describe("claimFromDoc", () => {
  it("inherits provenance (source, reliability, as-of) from the document", () => {
    const filing = DATA_ROOM.find((d) => d.id === "filing-q4")!;
    const claim = claimFromDoc(filing, {
      subject: "Northwind Robotics ARR",
      predicate: "reported",
      value: "$5.2M",
    });
    expect(claim.source).toBe(filing.source);
    expect(claim.source_reliability).toBe(filing.reliability);
    expect(claim.as_of).toBe(filing.as_of);
    expect(claim.value).toBe("$5.2M");
  });

  it("carries the higher trust of a filing over a deck for the same subject", () => {
    const deck = DATA_ROOM.find((d) => d.id === "deck-financials")!;
    const filing = DATA_ROOM.find((d) => d.id === "filing-q4")!;
    const fromDeck = claimFromDoc(deck, { subject: "ARR", predicate: "reported", value: "$8.0M" });
    const fromFiling = claimFromDoc(filing, { subject: "ARR", predicate: "reported", value: "$5.2M" });
    expect(fromFiling.source_reliability).toBeGreaterThan(fromDeck.source_reliability);
  });
});
