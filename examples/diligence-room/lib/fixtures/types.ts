/**
 * Types for the Due-Diligence Room data-room fixtures (DR-0b, docs/DILIGENCE.md).
 *
 * A "data room" is the curated set of source documents the specialist agents (DR-1) read.
 * The fixtures deliberately plant contradictions, temporal drift, and reliability variance
 * that ONLY a shared, bi-temporal, corroboration-aware memory can reconcile — and which the
 * golden run (DR-5a) will assert are surfaced.
 */

import type { SpecialistId } from "../types";

/** Source kinds, ordered elsewhere by trust prior (filing > contract/court > … > blog). */
export type DocType =
  | "audited_filing"
  | "signed_contract"
  | "court_record"
  | "data_export"
  | "cap_table"
  | "org_chart"
  | "technical_audit"
  | "news"
  | "press_release"
  | "pitch_deck"
  | "management_qa"
  | "blog";

/** One document in the data room. `as_of` is when its assertions are claimed to hold (§4/§12). */
export interface SourceDoc {
  id: string;
  title: string;
  type: DocType;
  /** Who produced it (used for provenance + related-party detection). */
  source: string;
  /** ISO date the document is published / its facts are asserted as-of. */
  as_of: string;
  /** 0..1 prior trust derived from `type` (see reliability.ts). */
  reliability: number;
  /** Which specialist agents should read this document. */
  forSpecialists: SpecialistId[];
  /** The document body — realistic prose embedding the claims the agents will extract. */
  text: string;
}

/** The kind of defect planted between documents — each maps to a core capability. */
export type DefectKind =
  | "temporal_drift" // later source supersedes an earlier claim (§12)
  | "contradiction" // two sources assert incompatible values (conflict detection, §8)
  | "reliability" // a low-trust source overstates vs a high-trust one (belief, CC-*)
  | "related_party" // a hidden ownership link across entities (graph/community, §9)
  | "stale"; // an old claim no later source refutes (belief stays moderate)

/** A planted defect: the oracle for acceptance + the golden run (DR-5a). */
export interface Defect {
  id: string;
  kind: DefectKind;
  /** One-line description of what a competent red-team should surface. */
  summary: string;
  /** The subject entity the defect concerns (for graph/claim lookup). */
  subject: string;
  /** Document ids involved (the earlier/lower-trust one first, by convention). */
  docs: string[];
  /** What the correct resolution is — the memo's expected finding. */
  resolution: string;
}
