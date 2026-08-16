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

export async function runSpecialist(
  config: SpecialistConfig,
  deps: { extract: ExtractFn; store: StoreClaimFn },
): Promise<SpecialistRun> {
  // Extract every document concurrently; `mapLimit` preserves input order, so storing below
  // stays in deterministic doc order — provenance and output are unchanged from the serial loop.
  const perDoc = await mapLimit(config.docs, EXTRACT_CONCURRENCY, async (doc) => ({
    doc,
    triples: await deps.extract(doc, config.mandate),
  }));

  const claims: SpecialistRun["claims"] = [];
  for (const { doc, triples } of perDoc) {
    for (const triple of triples) {
      await deps.store(config.id, doc, triple);
      claims.push({ doc: doc.id, triple });
    }
  }

  return {
    specialist: config.id,
    docsRead: config.docs.length,
    claimsWritten: claims.length,
    claims,
  };
}
