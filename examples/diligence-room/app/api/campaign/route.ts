/**
 * Run the whole due-diligence campaign for a Room in one call (DR-2):
 * dispatch specialists → flush → consolidate → red-team → flush → synthesis → memo.
 *
 * The orchestration lives in `runCampaign` as discrete, resumable steps; this route injects
 * the live (model + core) step functions. A Vercel Workflow would wrap each step as a durable
 * checkpoint — the sequence and step boundaries are already shaped for it.
 */

import { runCampaign } from "@/lib/campaign";
import { specialists } from "@/lib/agents/specialists";
import {
  liveConsolidate,
  liveFlush,
  liveRedTeam,
  liveSpecialist,
  liveSynthesis,
} from "@/lib/agents/live";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

export async function POST(req: Request) {
  const { roomId } = (await req.json()) as { roomId?: string };
  if (!roomId) {
    return Response.json({ error: "roomId is required" }, { status: 400 });
  }

  const result = await runCampaign(
    { roomId, specialists: specialists() },
    {
      runSpecialist: (config) => liveSpecialist(roomId, config),
      flush: () => liveFlush(roomId),
      consolidate: () => liveConsolidate(roomId),
      runRedTeam: () => liveRedTeam(roomId),
      runSynthesis: () => liveSynthesis(roomId),
    },
  );

  const ok = result.steps.every((s) => s.status !== "error");
  return Response.json(result, { status: ok ? 200 : 207 });
}
