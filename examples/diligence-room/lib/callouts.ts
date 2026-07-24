/**
 * "Why shared memory" callouts (DR-3g) — the closing argument. Small, pure content the UI
 * pins to each surface so a viewer can see *which* memory capability makes the result possible,
 * and a summary list for the memo: the things an isolated, single-context agent could not do.
 */

export interface Capability {
  title: string;
  blurb: string;
}

/** The capabilities this run proves — rendered as the memo's "why this needed shared memory". */
export const WHY_SHARED_MEMORY: Capability[] = [
  {
    title: "Shared multi-agent memory",
    blurb:
      "Four specialists wrote to one memory; the red-team read all of them at once. No single agent's context window ever held the whole picture.",
  },
  {
    title: "Bi-temporal supersession",
    blurb:
      "The audited March filing superseded the January deck — memory kept both versions and knew which one is current.",
  },
  {
    title: "Corroboration-weighted belief",
    blurb:
      "Claims are trusted by their source's reliability, so a signed contract outranks a blog rumor and an audit outranks a pitch deck.",
  },
  {
    title: "Provenance-tagged evidence",
    blurb:
      "Every finding in the memo traces back through its evidence chain to the exact source that grounds it — not the model's memory.",
  },
];

/** One-line capability captions pinned under each panel's title. */
export const PANEL_CAPTIONS = {
  rail: "Four specialists + red-team + synthesis — one shared memory.",
  feed: "Found by cross-examining every agent's claims at once — impossible with isolated context.",
  graph: "Entities, relationships, and belief the whole team can see.",
} as const;
