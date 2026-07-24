import { start } from "workflow/api";
import { runCampaignWorkflow } from "@/lib/workflow/campaign-workflow";

export const dynamic = "force-dynamic";

/**
 * DR-6a (PoC) — trigger the durable campaign workflow and return its runId immediately.
 * The workflow then runs each phase as an independent durable step (no 300s function cap);
 * the client polls /api/campaign/status?runId=… for progress (DR-6b/c).
 */
export async function POST(req: Request): Promise<Response> {
  const { roomId } = (await req.json().catch(() => ({}))) as { roomId?: string };
  if (!roomId) {
    return Response.json({ error: "roomId is required" }, { status: 400 });
  }
  const run = await start(runCampaignWorkflow, [roomId]);
  return Response.json({ runId: run.runId });
}
