/**
 * Run the red-team over a Room (DR-1c). Gathers every specialist's claims from shared memory
 * (MA-2 read across all agents), asks the LLM to find contradictions / drift / reliability /
 * related-party / stale disputes, and records each finding back to memory under agent_id
 * "redteam" (MA-4) so the synthesis agent inherits the reasoning. Flushes at the end (MA-1).
 */

import { getModel } from "@/lib/model";
import { makeDisputeFinder, runRedTeam, disputeText } from "@/lib/agents/redteam";
import { flush, gatherClaims, storeClaim } from "@/lib/core";
import type { Claim } from "@/lib/types";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const GATHER_QUERY =
  "Northwind Robotics revenue ARR retention churn customers litigation contracts ownership " +
  "related party technology uptime market footprint";

export async function POST(req: Request) {
  const { roomId } = (await req.json()) as { roomId?: string };
  if (!roomId) {
    return Response.json({ error: "roomId is required" }, { status: 400 });
  }

  try {
    const pool = await gatherClaims(roomId, GATHER_QUERY);
    const claims = pool.hits.map((h) => ({ text: h.text, agent_id: h.agent_id }));

    const run = await runRedTeam({
      claims,
      find: makeDisputeFinder(getModel()),
      record: async (d) => {
        // A dispute is recorded as a red-team claim: its confidence is the finding's reliability.
        const finding: Claim = {
          subject: d.subject,
          predicate: `dispute:${d.kind}`,
          value: disputeText(d),
          source: "red-team analysis",
          source_reliability: d.confidence,
        };
        await storeClaim(roomId, "redteam", finding, { sync: true });
      },
    });

    await flush(roomId, "redteam");
    return Response.json(run);
  } catch (err) {
    return Response.json(
      { error: err instanceof Error ? err.message : "red-team run failed" },
      { status: 502 },
    );
  }
}
