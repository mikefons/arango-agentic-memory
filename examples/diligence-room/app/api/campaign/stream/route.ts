/**
 * The campaign as a Server-Sent-Events stream (DR-3a) — the spine of the War Room.
 *
 * Live (a provider key is set and not `?canned=1`): runs the real campaign and pushes each
 * step, the red-team's disputes, and the memo as they are produced. Canned (no key, `?canned=1`,
 * or a live failure): replays the golden run's events, paced with delays. Both modes emit the
 * SAME event shape and order, so the client renders identically.
 */

import { runCampaign } from "@/lib/campaign";
import { goldenEvents } from "@/lib/campaign-stream";
import { specialists } from "@/lib/agents/specialists";
import {
  liveConsolidate,
  liveFlush,
  liveRedTeam,
  liveSpecialist,
  liveSynthesis,
} from "@/lib/agents/live";
import { hasProviderKey } from "@/lib/model";
import { GOLDEN_RUN } from "@/lib/fixtures/golden/run";
import type { CampaignEvent } from "@/lib/room-state";
import type { Dispute } from "@/lib/agents/redteam";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function GET(req: Request) {
  const url = new URL(req.url);
  const roomId = url.searchParams.get("roomId");
  const canned = url.searchParams.get("canned") === "1" || !roomId || !hasProviderKey();

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (ev: CampaignEvent) =>
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));

      try {
        if (canned) {
          for (const ev of goldenEvents(GOLDEN_RUN)) {
            send(ev);
            await sleep(ev.type === "step" ? 700 : 400);
          }
        } else {
          let disputes: Dispute[] = [];
          const result = await runCampaign(
            { roomId: roomId!, specialists: specialists() },
            {
              runSpecialist: (c) => liveSpecialist(roomId!, c),
              flush: () => liveFlush(roomId!),
              consolidate: () => liveConsolidate(roomId!),
              runRedTeam: async () => {
                const run = await liveRedTeam(roomId!);
                disputes = run.disputes;
                return run;
              },
              runSynthesis: () => liveSynthesis(roomId!),
              onStep: (step) => {
                send({ type: "step", step });
                if (step.name === "redteam" && step.status === "ok") {
                  send({ type: "disputes", disputes });
                }
              },
            },
          );
          if (result.memo) send({ type: "memo", memo: result.memo });
          send({ type: "done", ok: result.steps.every((s) => s.status !== "error") });
        }
      } catch {
        // Live run blew up mid-flight → finish with the canned run so the demo still completes.
        for (const ev of goldenEvents(GOLDEN_RUN)) send(ev);
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
