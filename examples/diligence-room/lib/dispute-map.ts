/**
 * Dispute ⇄ graph mapping (DR-3d) — kept pure so the cross-highlight is unit-testable.
 *
 * The red-team's disputes are prose ("Northwind ARR", winner "$5.2M (audited)", loser
 * "$8.0M (deck)"). The evidence graph is entities ("$5.2M ARR", "$8.0M", "Deck ARR"…). This
 * module bridges the two: given a dispute, `disputeNodeIds` returns the graph nodes the dispute
 * touches, so hovering a contradiction in the feed lights up exactly its cluster in the graph.
 */

import type { Dispute, DisputeKind } from "./agents/redteam";
import type { GraphViewNode } from "./room-state";

/** Presentation metadata per dispute kind (feed badge + accent). */
export interface DisputeKindMeta {
  label: string;
  glyph: string;
  /** CSS var name for the accent (defined in globals.css). */
  tone: "contradiction" | "temporal" | "reliability" | "related" | "stale";
}

export const DISPUTE_KIND: Record<DisputeKind, DisputeKindMeta> = {
  contradiction: { label: "Contradiction", glyph: "⚡", tone: "contradiction" },
  temporal_drift: { label: "Superseded", glyph: "↻", tone: "temporal" },
  reliability: { label: "Low-trust source", glyph: "◑", tone: "reliability" },
  related_party: { label: "Related party", glyph: "⚭", tone: "related" },
  stale: { label: "Stale", glyph: "◔", tone: "stale" },
};

// Tokens too common to be discriminating (the target itself + function words).
const STOP = new Set([
  "northwind",
  "robotics",
  "the",
  "a",
  "an",
  "of",
  "vs",
  "over",
  "and",
  "or",
  "is",
  "to",
  "by",
  "no",
  "not",
  "its",
  "on",
  "in",
  "at",
  "revenue", // too broad — "net revenue retention" vs "Orion Retail revenue"
]);

/** Normalize a phrase into discriminating tokens: lowercased, punctuation-stripped, len ≥ 2. */
export function terms(text: string): Set<string> {
  const out = new Set<string>();
  for (const raw of text.toLowerCase().split(/[^a-z0-9.]+/)) {
    const tok = raw.replace(/^\.+|\.+$/g, ""); // trim stray dots, keep internal (5.2m)
    if (tok.length >= 2 && !STOP.has(tok)) out.add(tok);
  }
  return out;
}

/** Drop the parenthetical source attribution — "$8.0M (deck)" → "$8.0M" — so a shared source
 *  word (deck, audit, management) doesn't bleed the highlight onto unrelated same-source claims. */
function stripSource(s: string): string {
  return s.replace(/\([^)]*\)/g, " ");
}

/** The dispute's discriminating terms — subject + the winner/loser *values* (source stripped). */
export function disputeTerms(d: Dispute): Set<string> {
  const t = new Set<string>();
  for (const part of [d.subject, stripSource(d.winner ?? ""), stripSource(d.loser ?? "")]) {
    for (const tok of terms(part)) t.add(tok);
  }
  return t;
}

/** Graph nodes whose name shares a discriminating token with the dispute. */
export function disputeNodeIds(d: Dispute, nodes: GraphViewNode[]): Set<string> {
  const want = disputeTerms(d);
  const hits = new Set<string>();
  for (const n of nodes) {
    const nodeTerms = terms(n.name);
    for (const tok of nodeTerms) {
      if (want.has(tok)) {
        hits.add(n.id);
        break;
      }
    }
  }
  return hits;
}
