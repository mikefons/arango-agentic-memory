import { dream } from "@/lib/core";
import { GUILD_TIER } from "@/lib/expedition";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";

// The keep dreams over the shared guild ledger (E-1) — that's where world facts live.
export async function POST() {
  try {
    return Response.json(await dream(TENANT, GUILD_TIER));
  } catch {
    return Response.json({ error: "dream failed" }, { status: 502 });
  }
}
