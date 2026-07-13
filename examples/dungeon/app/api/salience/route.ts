import { salience } from "@/lib/core";
import { GUILD_TIER } from "@/lib/expedition";

export const dynamic = "force-dynamic";

export async function POST() {
  try {
    return Response.json(await salience("dungeon-player", GUILD_TIER));
  } catch {
    return Response.json({ entities: 0 }, { status: 502 });
  }
}
