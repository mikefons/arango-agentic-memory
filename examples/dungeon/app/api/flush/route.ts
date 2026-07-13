import { flush } from "@/lib/core";
import { GUILD_TIER } from "@/lib/expedition";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";

// The chronicle barrier (MA-1): block until the departing hero's queued writes have
// committed and are retrievable, so the next hero's briefing includes their last turns.
export async function POST() {
  try {
    return Response.json(await flush({ tenant_id: TENANT, agent_id: GUILD_TIER }));
  } catch {
    return Response.json({ status: "timeout" }, { status: 502 });
  }
}
