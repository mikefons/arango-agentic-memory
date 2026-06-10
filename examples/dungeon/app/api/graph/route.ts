import { getEntity, listEntities } from "@/lib/core";
import { buildGraph } from "@/lib/graph";
import { DUNGEON } from "@/lib/world";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";
const ROOM_NAMES = Object.values(DUNGEON).map((r) => r.name);

export async function GET() {
  try {
    const list = async () => (await listEntities(TENANT)).entities;
    const get = async (id: string) => {
      try {
        return await getEntity(id, TENANT);
      } catch {
        return null;
      }
    };
    const graph = await buildGraph(list, get, ROOM_NAMES);
    return Response.json(graph);
  } catch {
    // Memory faults never break the UI (§15) — render an empty map.
    return Response.json({ nodes: [], edges: [] });
  }
}
