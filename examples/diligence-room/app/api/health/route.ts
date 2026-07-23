import { health } from "@/lib/core";

export const dynamic = "force-dynamic";

export async function GET() {
  const ok = await health();
  return Response.json({ ok }, { status: ok ? 200 : 503 });
}
