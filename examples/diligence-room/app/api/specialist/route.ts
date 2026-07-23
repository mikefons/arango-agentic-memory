/**
 * Run one specialist agent over its slice of the data room for a Room (DR-1a).
 * POST { roomId, specialist } → extracts claims with the LLM and writes them to shared memory
 * under the specialist's agent_id, then flushes so the writes are immediately visible (MA-1).
 *
 * This is the manual per-specialist trigger; DR-2 wraps the whole set in a durable campaign.
 */

import { getModel } from "@/lib/model";
import { makeExtractor } from "@/lib/agents/extract";
import { runSpecialist } from "@/lib/agents/specialist";
import { specialist } from "@/lib/agents/specialists";
import { flush, storeClaim } from "@/lib/core";
import { claimFromDoc } from "@/lib/claims";
import type { SpecialistId } from "@/lib/types";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(req: Request) {
  const { roomId, specialist: id } = (await req.json()) as {
    roomId?: string;
    specialist?: SpecialistId;
  };
  if (!roomId || !id) {
    return Response.json({ error: "roomId and specialist are required" }, { status: 400 });
  }
  const config = specialist(id);
  if (!config) {
    return Response.json({ error: `unknown or unwired specialist: ${id}` }, { status: 404 });
  }

  try {
    const extract = makeExtractor(getModel());
    const run = await runSpecialist(config, {
      extract,
      store: async (agentId, doc, triple) => {
        await storeClaim(roomId, agentId, claimFromDoc(doc, triple), { sync: true });
      },
    });
    await flush(roomId, id);
    return Response.json(run);
  } catch (err) {
    return Response.json(
      { error: err instanceof Error ? err.message : "specialist run failed" },
      { status: 502 },
    );
  }
}
