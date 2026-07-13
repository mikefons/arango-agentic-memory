import { store } from "@/lib/core";
import { GUILD_TIER } from "@/lib/expedition";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";

// The Chronicler writes an expedition's summary into the shared guild ledger (E-1),
// synchronously (MA-1) so the next hero reads it on turn 1. The dream pass is triggered
// separately by the client (reusing the existing consolidation flow).
export async function POST(req: Request) {
  try {
    const { summary } = (await req.json()) as { summary?: string };
    if (!summary) return Response.json({ ok: false }, { status: 400 });
    await store(summary, { tenant_id: TENANT, agent_id: GUILD_TIER }, { sync: true });
    return Response.json({ ok: true });
  } catch {
    // A chronicle failure must never trap the player — the expedition still ends.
    return Response.json({ ok: false }, { status: 502 });
  }
}
