"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { initialWarState, reduceCampaign, type WarState } from "@/lib/campaign-state";
import type { CampaignEvent } from "@/lib/room-state";

/**
 * Drives the War Room from the campaign SSE stream (DR-3c). `start()` opens the stream (canned
 * replay by default; the server falls back to canned when no provider key is set) and folds each
 * event through the reducer. Idempotent: a new start() resets and reconnects.
 */
export function useCampaign() {
  const [state, setState] = useState<WarState>(initialWarState);
  const esRef = useRef<EventSource | null>(null);

  const stop = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const start = useCallback(
    (opts?: { canned?: boolean; roomId?: string }) => {
      stop();
      setState(initialWarState());
      const params = opts?.canned === false && opts?.roomId ? `roomId=${opts.roomId}` : "canned=1";
      const es = new EventSource(`/api/campaign/stream?${params}`);
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

  useEffect(() => () => stop(), [stop]);

  return { state, start };
}
