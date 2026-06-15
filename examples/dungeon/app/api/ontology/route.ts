import { ontologyProposals, ontologyScan } from "@/lib/core";
import type { ProposalList } from "@/lib/ontology";

export const dynamic = "force-dynamic";

const TENANT = "dungeon-player";

// List proposals. A 404 from the core means ONTOLOGY_EVOLUTION is off → disabled.
export async function GET() {
  try {
    const proposals = await ontologyProposals(TENANT);
    return Response.json({ enabled: true, proposals } satisfies ProposalList);
  } catch {
    return Response.json({ enabled: false, proposals: [] } satisfies ProposalList);
  }
}

// Trigger a proposal scan.
export async function POST() {
  try {
    return Response.json(await ontologyScan(TENANT, "dm"));
  } catch {
    return Response.json({ clusters: 0, proposed: 0, error: "disabled" }, { status: 502 });
  }
}
