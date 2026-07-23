/**
 * The specialist orchestrator (DR-1). Runs one specialist over its slice of the data room:
 * for each document, extract claim triples and write each to the Room's shared memory under
 * the specialist's own `agent_id` (MA-2 provenance) with the document's reliability prior
 * (CC-*). Both the extractor and the store fn are injected, so this is fully unit-testable
 * without an LLM or a running core.
 */

import type { SpecialistConfig, SpecialistRun, ExtractFn, StoreClaimFn } from "./types";

export async function runSpecialist(
  config: SpecialistConfig,
  deps: { extract: ExtractFn; store: StoreClaimFn },
): Promise<SpecialistRun> {
  const claims: SpecialistRun["claims"] = [];

  for (const doc of config.docs) {
    const triples = await deps.extract(doc, config.mandate);
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
