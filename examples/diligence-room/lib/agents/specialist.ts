/**
 * The specialist orchestrator (DR-1). Runs one specialist over its slice of the data room:
 * for each document, extract claim triples and write each to the Room's shared memory under
 * the specialist's own `agent_id` (MA-2 provenance) with the document's reliability prior
 * (CC-*). Both the extractor and the store fn are injected, so this is fully unit-testable
 * without an LLM or a running core.
 */

import { mapLimit } from "../concurrency";
import type { SpecialistConfig, SpecialistRun, ExtractFn, StoreClaimFn } from "./types";

// Per-document extraction is an independent LLM call, so run the slice concurrently (bounded)
// rather than one doc at a time — that serial loop was the slow part of a run (the app-level
// version of the core's IN-7 extraction pool). Override the fan-out with
// DILIGENCE_EXTRACT_CONCURRENCY; keep it modest so a big data room doesn't trip provider limits.
const EXTRACT_CONCURRENCY = Number(process.env.DILIGENCE_EXTRACT_CONCURRENCY ?? 5);

// Claim writes run with bounded concurrency. Enqueueing is cheap, but with the opt-in sync
// writes (DILIGENCE_SYNC_WRITES=1) each store blocks on a commit, so a serial loop would be the
// long pole. Override with DILIGENCE_STORE_CONCURRENCY.
const STORE_CONCURRENCY = Number(process.env.DILIGENCE_STORE_CONCURRENCY ?? 4);

export async function runSpecialist(
  config: SpecialistConfig,
  deps: { extract: ExtractFn; store: StoreClaimFn },
): Promise<SpecialistRun> {
  // Extract every document concurrently; `mapLimit` preserves input order, so the claim list
  // (and the order writes start in) stays deterministic doc order.
  const perDoc = await mapLimit(config.docs, EXTRACT_CONCURRENCY, async (doc) => ({
    doc,
    triples: await deps.extract(doc, config.mandate),
  }));

  const claims: SpecialistRun["claims"] = perDoc.flatMap(({ doc, triples }) =>
    triples.map((triple) => ({ doc: doc.id, triple })),
  );
  const docById = new Map(config.docs.map((d) => [d.id, d]));
  await mapLimit(claims, STORE_CONCURRENCY, ({ doc, triple }) =>
    deps.store(config.id, docById.get(doc)!, triple),
  );

  return {
    specialist: config.id,
    docsRead: config.docs.length,
    claimsWritten: claims.length,
    claims,
  };
}
