"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { initialWarState, reduceCampaign, type WarState } from "@/lib/campaign-state";
import type { CampaignEvent } from "@/lib/room-state";

/**
 * Drives the War Room from the campaign SSE stream.
 *
 * - Canned (default): connects straight to the golden replay (`?canned=1`).
 * - Live (DR-6): POST /api/campaign/start kicks off a durable Workflow run (background, no 300s
 *   cap) and returns a runId; we then stream `?runId=…`. The runId is pushed to the URL so a
 *   reload re-attaches to the in-flight run instead of starting a new one.
 *
 * Either way the events fold through the same reducer.
 */
export function useCampaign() {
  const [state, setState] = useState<WarState>(initialWarState);
  const esRef = useRef<EventSource | null>(null);

  const stop = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  /** Attach to a run/replay stream and fold its events. */
  const attach = useCallback(
    (query: string) => {
      const es = new EventSource(`/api/campaign/stream?${query}`);
      esRef.current = es;
      es.onmessage = (e) => {
        const ev = JSON.parse(e.data) as CampaignEvent;
        setState((s) => reduceCampaign(s, ev));
        if (ev.type === "done") stop();
      };
      es.onerror = () => stop();
    },
    [stop],
  );

  const start = useCallback(
    async (opts?: { canned?: boolean; roomId?: string }) => {
      stop();
      setState(initialWarState());

      if (opts?.canned === false && opts?.roomId) {
        // Live: trigger the durable workflow, then stream its run.
        try {
          const res = await fetch("/api/campaign/start", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ roomId: opts.roomId }),
          });
          const { runId } = (await res.json()) as { runId?: string };
          if (runId) {
            history.replaceState(null, "", `?runId=${encodeURIComponent(runId)}`);
            attach(`runId=${encodeURIComponent(runId)}`);
            return;
          }
        } catch {
          // fall through to canned if the trigger fails
        }
      }
      attach("canned=1");
    },
    [attach, stop],
  );

  // Re-attach to an in-flight run after a reload (runId persisted in the URL).
  useEffect(() => {
    const runId = new URLSearchParams(window.location.search).get("runId");
    if (runId) attach(`runId=${encodeURIComponent(runId)}`);
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { state, start };
}
