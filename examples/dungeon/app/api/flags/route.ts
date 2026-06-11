import { getFlags } from "@/lib/flags";

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json(await getFlags());
}
