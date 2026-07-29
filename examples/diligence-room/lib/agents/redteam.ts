/**
 * The red-team agent (DR-1c) — the demo's showstopper.
 *
 * Unlike the specialists, the red-team reads no documents. It reads the *shared memory* — every
 * specialist's claims, across their agent_ids (MA-2) — and hunts for what no single specialist
 * could see: claims that CONTRADICT each other, claims a later source SUPERSEDES, low-trust
 * claims that overstate vs a high-trust one, hidden RELATED-PARTY links, and STALE claims. Each
 * dispute it finds is written back to memory under its own agent_id (MA-4 capture) so the
 * synthesis agent inherits the reasoning, not just the raw facts.
 *
 * The LLM (dispute finder) and the core (gather + record) are both injected, so the
 * orchestration is unit-testable without a key or a running core.
 */

import { generateObject, type LanguageModel } from "ai";
import { z } from "zod";
import { agentSignal } from "../model";

/** The kinds of dispute the red-team can raise (mirror the planted-defect kinds). */
export type DisputeKind =
  | "contradiction"
  | "temporal_drift"
  | "reliability"
  | "related_party"
  | "stale";

export interface Dispute {
  subject: string;
  kind: DisputeKind;
  /** What the red-team concluded — the finding the memo will carry. */
  summary: string;
  /** The claim/value the red-team trusts (higher reliability or later date), if any. */
  winner?: string;
  /** The claim/value it is superseded/contradicted by, if any. */
  loser?: string;
  /** 0..1 confidence in the finding — becomes the reliability of the recorded finding. */
  confidence: number;
}

/** A claim as seen in memory (from a retrieve hit): its text + who wrote it. */
export interface ClaimRecord {
  text: string;
  agent_id?: string;
}

/** Finds disputes across a set of claims. LLM-backed in prod, faked in tests. */
export type DisputeFinder = (claims: ClaimRecord[]) => Promise<Dispute[]>;

/** Records one dispute as a red-team finding in shared memory. */
export type RecordDisputeFn = (d: Dispute) => Promise<void>;

export interface RedTeamRun {
  claimsReviewed: number;
  disputes: Dispute[];
}

const DisputeSchema = z.object({
  disputes: z
    .array(
      z.object({
        subject: z.string().describe("The entity the dispute concerns, e.g. 'Northwind ARR'"),
        kind: z.enum(["contradiction", "temporal_drift", "reliability", "related_party", "stale"]),
        summary: z.string().describe("One sentence: what the discrepancy is and the resolution."),
        winner: z.string().optional().describe("The value/claim to trust."),
        loser: z.string().optional().describe("The value/claim it supersedes or contradicts."),
        confidence: z.number().min(0).max(1),
      }),
    )
    .describe("Every material discrepancy found across the claims."),
});

const SYSTEM =
  "You are the red-team on a due-diligence deal. You are given the claims that specialist " +
  "analysts wrote to shared memory, each tagged with its source and an 'as of' date. Find every " +
  "material discrepancy a partner must know before investing:\n" +
  "- contradiction: two claims assert incompatible values for the same subject.\n" +
  "- temporal_drift: a later-dated source supersedes an earlier claim (prefer the later/audited one).\n" +
  "- reliability: a low-trust source (blog, management deck) overstates vs a high-trust one (audited filing, signed contract).\n" +
  "- related_party: a 'customer' or counterparty is secretly owned by or affiliated with an investor/officer.\n" +
  "- stale: an old claim no later source refutes — flag as unverified-current.\n" +
  "Only raise disputes grounded in the claims provided. For each, name the winner and loser and " +
  "your confidence. Do not invent facts.";

/** Build a DisputeFinder bound to a model. */
export function makeDisputeFinder(model: LanguageModel): DisputeFinder {
  return async (claims: ClaimRecord[]): Promise<Dispute[]> => {
    const rendered = claims
      .map((c, i) => `${i + 1}. [${c.agent_id ?? "?"}] ${c.text}`)
      .join("\n");
    const { object } = await generateObject({
      model,
      schema: DisputeSchema,
      system: SYSTEM,
      abortSignal: agentSignal(),
      prompt: `Claims in shared memory:\n${rendered}\n\nFind the disputes.`,
    });
    return object.disputes;
  };
}

/** Run the red-team: gather claims → find disputes → record each as a finding. */
export async function runRedTeam(deps: {
  claims: ClaimRecord[];
  find: DisputeFinder;
  record: RecordDisputeFn;
}): Promise<RedTeamRun> {
  const disputes = await deps.find(deps.claims);
  for (const d of disputes) {
    await deps.record(d);
  }
  return { claimsReviewed: deps.claims.length, disputes };
}

/** Canonical memory content for a recorded dispute (inherited by synthesis, MA-4). */
export function disputeText(d: Dispute): string {
  const vs = d.winner && d.loser ? ` Trust "${d.winner}" over "${d.loser}".` : "";
  return `RED-TEAM (${d.kind}): ${d.subject} — ${d.summary}${vs}`;
}
