/**
 * The synthesis agent (DR-1d) — turns the team's shared memory into the deliverable.
 *
 * It primes across everyone (specialists + the red-team, via read_agent_ids/MA-3) to get one
 * budgeted briefing, then writes an **investment memo**: a recommendation, a thesis, and a set
 * of findings each carrying an *evidence chain* (the claims/sources behind it) — so every
 * conclusion traces back to shared, provenance-tagged memory, not the model's imagination.
 *
 * The LLM (memo writer) is injected, so orchestration is unit-testable without a key.
 */

import { generateObject, type LanguageModel } from "ai";
import { z } from "zod";

export type Recommendation = "proceed" | "proceed_with_conditions" | "pass";

export interface Finding {
  title: string;
  /** "risk" (a red flag) or "strength". */
  kind: "risk" | "strength";
  detail: string;
  /** The evidence chain: the claims/sources that ground this finding. */
  evidence: string[];
  /** 0..1 confidence, informed by source reliability + corroboration. */
  confidence: number;
}

export interface Memo {
  target: string;
  recommendation: Recommendation;
  thesis: string;
  findings: Finding[];
}

/** Writes a memo from a primed briefing. LLM-backed in prod, faked in tests. */
export type MemoWriter = (briefing: string) => Promise<Memo>;

const MemoSchema = z.object({
  target: z.string(),
  recommendation: z.enum(["proceed", "proceed_with_conditions", "pass"]),
  thesis: z.string().describe("Two or three sentences: the investment thesis and the verdict."),
  findings: z
    .array(
      z.object({
        title: z.string(),
        kind: z.enum(["risk", "strength"]),
        detail: z.string(),
        evidence: z
          .array(z.string())
          .describe("The specific claims/sources that support this finding."),
        confidence: z.number().min(0).max(1),
      }),
    )
    .describe("The material findings, red flags first, each with its evidence chain."),
});

const SYSTEM =
  "You are the partner writing the investment memo for a due-diligence deal. You are given a " +
  "briefing assembled from the deal team's shared memory: the specialists' claims and the " +
  "red-team's disputes, each tagged with source and reliability. Write a decision-useful memo. " +
  "Ground every finding in the briefing — cite the specific claims/sources as its evidence " +
  "chain. Prefer audited/high-trust and later-dated facts over management claims. Lead with the " +
  "risks the red-team surfaced. Do not invent facts not present in the briefing.";

/** Build a MemoWriter bound to a model. */
export function makeMemoWriter(model: LanguageModel): MemoWriter {
  return async (briefing: string): Promise<Memo> => {
    const { object } = await generateObject({
      model,
      schema: MemoSchema,
      system: SYSTEM,
      prompt: `Deal-team briefing (from shared memory):\n${briefing}\n\nWrite the investment memo.`,
    });
    return object;
  };
}

/** Run synthesis: turn a primed briefing into the memo. */
export async function runSynthesis(briefing: string, write: MemoWriter): Promise<Memo> {
  return write(briefing);
}

/** One-line executive summary of a memo, for the war-room header / MA-4 capture. */
export function memoHeadline(memo: Memo): string {
  const risks = memo.findings.filter((f) => f.kind === "risk").length;
  return `${memo.target}: ${memo.recommendation.replace(/_/g, " ")} — ${risks} risk(s) flagged.`;
}
