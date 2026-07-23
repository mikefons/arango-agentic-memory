/**
 * The Room's evidence graph as a view model (DR-3a). Live from the core, or the canned golden
 * snapshot (`?canned=1`, or automatically if the core is unreachable). Node size = salience,
 * hue = community (related parties cluster), ring = belief.
 */

import { memoryGraph } from "@/lib/core";
import { toGraphView, type CoreGraph } from "@/lib/room-state";
import { GOLDEN_GRAPH } from "@/lib/fixtures/golden/run";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const roomId = url.searchParams.get("roomId");
  const canned = url.searchParams.get("canned") === "1" || !roomId;
  const limit = Number(url.searchParams.get("limit") ?? 40);

  if (canned) {
    return Response.json(toGraphView(GOLDEN_GRAPH, { limit }));
  }
  try {
    const graph = (await memoryGraph(roomId!)) as CoreGraph;
    return Response.json(toGraphView(graph, { limit }));
  } catch {
    // Core unreachable → fall back to the canned graph so the UI always renders.
    return Response.json(toGraphView(GOLDEN_GRAPH, { limit }));
  }
}
