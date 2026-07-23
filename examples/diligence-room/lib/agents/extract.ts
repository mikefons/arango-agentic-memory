/**
 * The LLM-backed claim extractor (DR-1). Given a document and a specialist's mandate, pull
 * out `{subject, predicate, value}` triples. Uses AI SDK `generateObject` with a Zod schema
 * so the output is structured, not free text. This is the only place the model is invoked;
 * `runSpecialist` takes an `ExtractFn`, so tests inject a deterministic fake instead.
 */

import { generateObject, type LanguageModel } from "ai";
import { z } from "zod";
import type { SourceDoc } from "../fixtures/types";
import type { ExtractFn, Triple } from "./types";

const ClaimSchema = z.object({
  claims: z
    .array(
      z.object({
        subject: z
          .string()
          .describe("The entity the claim is about, e.g. 'Northwind Robotics ARR'"),
        predicate: z.string().describe("A short relation, e.g. 'reported', 'is', 'owns'"),
        value: z.string().describe("The asserted fact/value, e.g. '$5.2M' or 'no litigation'"),
      }),
    )
    .describe("The distinct factual claims stated in the document."),
});

const SYSTEM =
  "You are a due-diligence analyst extracting verifiable claims from a source document. " +
  "Return only claims the document actually asserts — do not infer, speculate, or add outside " +
  "knowledge. Keep each claim atomic: one subject, one predicate, one value. Use the exact " +
  "figures and names from the text.";

/** Build an ExtractFn bound to a model. */
export function makeExtractor(model: LanguageModel): ExtractFn {
  return async (doc: SourceDoc, mandate: string): Promise<Triple[]> => {
    const { object } = await generateObject({
      model,
      schema: ClaimSchema,
      system: SYSTEM,
      prompt:
        `Mandate: ${mandate}\n\n` +
        `Document (${doc.type}, source: ${doc.source}, as of ${doc.as_of}):\n${doc.text}\n\n` +
        "Extract the claims relevant to the mandate.",
    });
    return object.claims;
  };
}
