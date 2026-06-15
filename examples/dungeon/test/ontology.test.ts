import { describe, expect, it } from "vitest";
import { proposalSummary, type Proposal } from "../lib/ontology";

const proposal: Proposal = {
  _key: "t__Person__Company",
  label_a: "Person",
  label_b: "Company",
  proposed_relationship: "works_at",
  support: 7,
  status: "pending",
};

describe("proposalSummary", () => {
  it("formats a one-line summary", () => {
    expect(proposalSummary(proposal)).toBe("Person → Company : works_at (7)");
  });
});
