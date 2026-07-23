/** Agent-layer types (DR-1). The LLM and the core are both injectable so orchestration is testable. */

import type { SpecialistId } from "../types";
import type { SourceDoc } from "../fixtures/types";

/** A raw extracted fact, before provenance is attached (that comes from the source document). */
export interface Triple {
  subject: string;
  predicate: string;
  value: string;
}

/** Extracts claim triples from one document. LLM-backed in prod, faked in tests. */
export type ExtractFn = (doc: SourceDoc, mandate: string) => Promise<Triple[]>;

/** Persists one claim to the Room's shared memory. Real `storeClaim` in prod, faked in tests. */
export type StoreClaimFn = (
  agentId: SpecialistId,
  doc: SourceDoc,
  triple: Triple,
) => Promise<void>;

/** A specialist's static config: who it is, what it's looking for, which docs it reads. */
export interface SpecialistConfig {
  id: SpecialistId;
  /** Human label for the UI. */
  title: string;
  /** The extraction mandate — what this specialist should pull from its documents. */
  mandate: string;
  /** The documents this specialist reads (its slice of the data room). */
  docs: SourceDoc[];
}

/** Result of running one specialist over its slice. */
export interface SpecialistRun {
  specialist: SpecialistId;
  docsRead: number;
  claimsWritten: number;
  /** The claims written, for the war-room timeline (DR-3). */
  claims: { doc: string; triple: Triple }[];
}
