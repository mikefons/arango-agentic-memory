/**
 * "Why shared memory" callouts (DR-3g) — the closing argument. Small, pure content the UI
 * pins to each surface so a viewer can see *which* memory capability makes the result possible,
 * and a summary list for the memo: the things an isolated, single-context agent could not do.
 */

export interface Capability {
  /** The memory capability, as a plain name. */
  title: string;
  /** Why it matters — the failure a single-context agent hits (business framing). */
  why: string;
  /** How arango-agentic-memory achieves it — the mechanism + the real feature (technical framing). */
  how: string;
}

/** The one-line thesis that frames the takeaway: the point of the whole demo. */
export const TAKEAWAY_LEDE =
  "A single-context agent couldn't have produced this memo. Four capabilities of arango-agentic-memory made it possible:";

/**
 * The capabilities this run proves — rendered as the memo's "why this needed shared memory"
 * takeaway. Each splits the value (`why`, for the business viewer) from the mechanism (`how`,
 * for the technical evaluator, revealed on demand).
 */
export const WHY_SHARED_MEMORY: Capability[] = [
  {
    title: "Shared multi-agent memory",
    why: "Four specialists worked in parallel — none ever held the whole deal in one context window — yet the team reached a single, coherent verdict.",
    how: "One ArangoDB tenant per Room. Each agent writes under its own agent_id; the red-team and synthesis read across all of them in one fused pass (read_agent_ids, MA-2), with a read-your-writes barrier (/v1/flush, MA-1) between phases so no agent reads stale memory.",
  },
  {
    title: "Bi-temporal supersession",
    why: "Sources contradict over time — a January pitch deck vs. an audited March filing. The system has to know which is current without discarding the history.",
    how: "Every memory carries a valid_time; a newer, higher-reliability claim writes a Supersedes edge to the one it replaces (DESIGN §4/§13). Retrieval returns the current fact, but the superseded chain stays auditable.",
  },
  {
    title: "Corroboration-weighted belief",
    why: "Not every source deserves equal trust — a signed contract should outweigh a blog rumor, and independent sources agreeing should compound confidence.",
    how: "Each claim carries its source_reliability; belief accrues as belief = confidence × (1 − (1−base)^Σreliability), so corroboration from independent sources raises it while a lone weak source stays low (CC-*).",
  },
  {
    title: "Provenance-tagged evidence",
    why: "An investment memo you can't audit is worthless — every claim has to trace to a real document, not the model's recollection.",
    how: "Each claim stores the exact source document it came from; the memo's evidence chain walks finding → claim → source, so every line is grounded in retrieved memory, not generated text.",
  },
];

/** One-line capability captions pinned under each panel's title. */
export const PANEL_CAPTIONS = {
  rail: "Four specialists + red-team + synthesis — one shared memory.",
  feed: "Found by cross-examining every agent's claims at once — impossible with isolated context.",
  graph: "Entities, relationships, and belief the whole team can see.",
} as const;
