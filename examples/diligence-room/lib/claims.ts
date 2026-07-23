/**
 * The claim model (DR-0c) — the convention for turning what a specialist agent *finds* into
 * a memory the whole Room can reason over.
 *
 * A claim is a `{subject, predicate, value}` triple plus provenance: which source asserted
 * it, how much that source is trusted (reliability prior → belief, CC-*), and the instant it
 * holds as-of (bi-temporal, §4/§12 — this is what lets a March filing *supersede* a January
 * deck). The canonical `claimText` embeds the subject/value/time so the core's entity
 * extractor and the red-team agent both have what they need; `source_reliability` is threaded
 * separately as a store param so it weights belief rather than polluting the text.
 */

import type { Claim } from "./types";
import type { SourceDoc } from "./fixtures/types";

/**
 * Canonical memory content for a claim. Self-describing: subject/predicate/value + provenance
 * + an "as of <date>" the core can parse into valid-time (bi-temporal supersession).
 */
export function claimText(claim: Claim): string {
  const asOf = claim.as_of ? ` (as of ${claim.as_of})` : "";
  return `${claim.subject} — ${claim.predicate}: ${claim.value}. Source: ${claim.source}${asOf}.`;
}

/**
 * Build a Claim from a data-room document and an extracted triple, inheriting the document's
 * source, reliability prior, and as-of date. This is the bridge DR-1's specialists use: they
 * decide the `{subject, predicate, value}`; provenance comes from the document they read.
 */
export function claimFromDoc(
  d: Pick<SourceDoc, "source" | "reliability" | "as_of">,
  triple: { subject: string; predicate: string; value: string },
): Claim {
  return {
    subject: triple.subject,
    predicate: triple.predicate,
    value: triple.value,
    source: d.source,
    source_reliability: d.reliability,
    as_of: d.as_of,
  };
}
