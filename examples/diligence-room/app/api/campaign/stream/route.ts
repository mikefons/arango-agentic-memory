/**
 * The campaign as a Server-Sent-Events stream — the spine of the War Room.
 *
 * - `?runId=…` (DR-6): replay a durable Workflow run's event stream as it is produced. The
 *   campaign itself runs as background durable steps (no 300s cap); this route just forwards the
 *   run's stream and can be re-attached after a disconnect.
 * - `?canned=1` / no provider key / a read failure: replay the golden run, paced with delays.
 *
 * All modes emit the SAME event shape and order, so the client renders identically.
 */

import { getRun } from "workflow/api";
import { goldenEvents } from "@/lib/campaign-stream";
import { GOLDEN_RUN } from "@/lib/fixtures/golden/run";
import type { CampaignEvent } from "@/lib/room-state";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function GET(req: Request) {
  const url = new URL(req.url);
  const runId = url.searchParams.get("runId");

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (ev: CampaignEvent) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));
      const sendRaw = (line: string) => controller.enqueue(encoder.encode(`data: ${line}\n\n`));

      try {
        if (runId) {
          // Durable: forward the workflow run's NDJSON event stream as SSE.
          const readable = getRun(runId).readable as unknown as ReadableStream<Uint8Array>;
          const reader = readable.getReader();
          const decoder = new TextDecoder();
          let buf = "";
          let finished = false;
          while (!finished) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let nl: number;
            while ((nl = buf.indexOf("\n")) >= 0) {
              const line = buf.slice(0, nl).trim();
              buf = buf.slice(nl + 1);
              if (!line) continue;
              sendRaw(line);
              try {
                if ((JSON.parse(line) as CampaignEvent).type === "done") finished = true;
              } catch {
                // ignore non-JSON keepalive lines
              }
            }
          }
        } else {
          // Canned golden replay (default + stage-safe fallback).
          for (const ev of goldenEvents(GOLDEN_RUN)) {
            send(ev);
            await sleep(ev.type === "step" ? 700 : 400);
          }
        }
      } catch {
        // A live read blew up → finish with the canned run so the demo still completes.
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
