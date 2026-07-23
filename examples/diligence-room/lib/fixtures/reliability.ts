/**
 * Source-reliability priors (DR-0b). A claim's trust starts from the *kind* of source it
 * came from; corroboration by independent sources raises belief in memory (CC-*). These
 * priors encode the analyst's rule of thumb: an audited filing outweighs a filing outweighs
 * news outweighs a management claim outweighs a blog rumor.
 */

import type { DocType } from "./types";

/** Prior trust in [0,1] for each document kind. */
export const RELIABILITY: Record<DocType, number> = {
  audited_filing: 0.95,
  signed_contract: 0.9,
  court_record: 0.9,
  data_export: 0.85,
  cap_table: 0.8,
  org_chart: 0.8,
  technical_audit: 0.8,
  news: 0.6,
  press_release: 0.45,
  management_qa: 0.4,
  pitch_deck: 0.4,
  blog: 0.25,
};

/** The reliability prior for a document kind. */
export function sourceReliability(type: DocType): number {
  return RELIABILITY[type];
}
