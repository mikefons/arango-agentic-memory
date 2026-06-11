import { dream } from "@/lib/core";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";
const AGENT = "dm";

export async function POST() {
  try {
    return Response.json(await dream(TENANT, AGENT));
  } catch {
    return Response.json({ error: "dream failed" }, { status: 502 });
  }
}
