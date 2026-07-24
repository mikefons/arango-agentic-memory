import { resetRoom } from "@/lib/core";

export const dynamic = "force-dynamic";

/**
 * Reset a Room's shared memory so a live campaign starts from a clean slate (tenant-scoped
 * soft-delete via the core's /v1/forget). Only ever affects `room:<id>` — safe to run between
 * live demos; never touches another demo's tenant or drops a collection.
 */
export async function POST(req: Request): Promise<Response> {
  const { roomId } = (await req.json().catch(() => ({}))) as { roomId?: string };
  if (!roomId) {
    return Response.json({ error: "roomId is required" }, { status: 400 });
  }
  try {
    const result = await resetRoom(roomId);
    return Response.json(result);
  } catch (e) {
    return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502 });
  }
}
