import { memoryGraph } from "@/lib/core";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";

export async function GET() {
  try {
    return Response.json(await memoryGraph(TENANT));
  } catch {
    // Memory faults never break the UI (§15) — render an empty graph.
    return Response.json({ nodes: [], edges: [] });
  }
}
