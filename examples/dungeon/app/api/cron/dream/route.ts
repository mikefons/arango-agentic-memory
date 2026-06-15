import { community, dream } from "@/lib/core";

// Nightly "the dungeon dreams" — Vercel Cron hits this on a schedule (see
// vercel.json). Only runs on a Vercel deployment; locally use the ✦ dream button.
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    // detect communities first so Dream State can scope conflict review to them
    await community("dungeon-player", "dm").catch(() => undefined);
    return Response.json(await dream("dungeon-player", "dm"));
  } catch {
    return Response.json({ error: "dream failed" }, { status: 502 });
  }
}
