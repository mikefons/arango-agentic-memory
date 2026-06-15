import { community } from "@/lib/core";

export const dynamic = "force-dynamic";

export async function POST() {
  try {
    return Response.json(await community("dungeon-player", "dm"));
  } catch {
    return Response.json({ entities: 0, communities: 0 }, { status: 502 });
  }
}
