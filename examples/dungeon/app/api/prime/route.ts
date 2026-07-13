import { prime } from "@/lib/core";
import { GUILD_TIER } from "@/lib/expedition";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";
const EMPTY = { context: "", hits: [], entities: [], steps: [], tokens_injected: 0 };

// Brief the next hero (MA-3): the guild ledger + the hero's own memory, in one pass.
export async function POST(req: Request) {
  try {
    const { task, heroId } = (await req.json()) as { task?: string; heroId?: string };
    if (!task || !heroId) return Response.json(EMPTY, { status: 400 });
    const result = await prime(task, {
      tenant_id: TENANT,
      agent_id: heroId,
      read_agent_ids: [heroId, GUILD_TIER],
    });
    return Response.json(result);
  } catch {
    return Response.json(EMPTY, { status: 502 }); // a failed briefing never traps the game
  }
}
