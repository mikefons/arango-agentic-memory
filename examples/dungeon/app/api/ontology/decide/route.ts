import { ontologyDecide } from "@/lib/core";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";

export async function POST(req: Request) {
  try {
    const { key, decision } = (await req.json()) as {
      key?: string;
      decision?: "approve" | "reject";
    };
    if (!key || (decision !== "approve" && decision !== "reject")) {
      return Response.json({ error: "key and decision required" }, { status: 400 });
    }
    return Response.json(await ontologyDecide(TENANT, "dm", key, decision));
  } catch {
    return Response.json({ status: "error" }, { status: 502 });
  }
}
