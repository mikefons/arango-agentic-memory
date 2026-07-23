/**
 * Run synthesis over a Room (DR-1d). Primes across the whole team (specialists + red-team,
 * MA-3), writes the investment memo with evidence chains, and captures a one-line verdict back
 * to memory under agent_id "synthesis" (MA-4). Returns the full memo for the war-room UI (DR-3).
 */

import { getModel } from "@/lib/model";
import { makeMemoWriter, memoHeadline, runSynthesis } from "@/lib/agents/synthesis";
import { flush, prime, storeClaim } from "@/lib/core";
import type { Claim } from "@/lib/types";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const TASK =
  "Assess whether to invest in Northwind Robotics. Weigh the specialists' findings and the " +
  "red-team's disputes; flag material risks and give a recommendation.";

export async function POST(req: Request) {
  const { roomId } = (await req.json()) as { roomId?: string };
  if (!roomId) {
    return Response.json({ error: "roomId is required" }, { status: 400 });
  }

  try {
    const briefing = await prime(roomId, TASK);
    if (!briefing.context) {
      return Response.json({ error: "no team memory to synthesize yet" }, { status: 409 });
    }
    const memo = await runSynthesis(briefing.context, makeMemoWriter(getModel()));

    // Capture the verdict so it persists in the Room's memory (MA-4).
    const verdict: Claim = {
      subject: memo.target,
      predicate: "memo:verdict",
      value: memoHeadline(memo),
      source: "synthesis",
      source_reliability: 0.9,
    };
    await storeClaim(roomId, "synthesis", verdict, { sync: true });
    await flush(roomId, "synthesis");

    return Response.json({ memo, tokens_injected: briefing.tokens_injected });
  } catch (err) {
    return Response.json(
      { error: err instanceof Error ? err.message : "synthesis failed" },
      { status: 502 },
    );
  }
}
