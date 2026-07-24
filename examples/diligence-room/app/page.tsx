"use client";

import { useEffect, useState } from "react";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { AgentRail } from "@/components/AgentRail";
import { Pipeline } from "@/components/Pipeline";
import { ContradictionFeed } from "@/components/ContradictionFeed";
import { Narration } from "@/components/Narration";
import { MemoPanel } from "@/components/MemoPanel";
import { RECOMMENDATION } from "@/lib/memo-export";
import { useCampaign } from "@/components/useCampaign";

type CoreState = "checking" | "online" | "offline";

// The room the live campaign writes to. Must match the core key's tenant (`room:<id>`)
// when the core enforces auth — see README "Actually driving the core live".
const ROOM_ID = process.env.NEXT_PUBLIC_DILIGENCE_ROOM ?? "northwind";

export default function WarRoom() {
  const [core, setCore] = useState<CoreState>("checking");
  const [active, setActive] = useState<number | null>(null);
  const [memoOpen, setMemoOpen] = useState(false);
  const [live, setLive] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetNonce, setResetNonce] = useState(0);
  const { state, start } = useCampaign();

  // Clear the Room's shared memory so a live run starts clean (tenant-scoped, safe to repeat).
  const handleReset = async () => {
    setResetting(true);
    try {
      await fetch("/api/reset", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ roomId: ROOM_ID }),
      });
      setResetNonce((n) => n + 1); // reload the (now-empty) live graph
    } finally {
      setResetting(false);
    }
  };

  // Drop the cross-highlight + close the memo whenever a new run starts.
  useEffect(() => {
    if (state.run === "running") {
      setActive(null);
      setMemoOpen(false);
    }
  }, [state.run]);

  // Auto-open the memo when the deal team finishes — the deliverable is the payoff.
  useEffect(() => {
    if (state.run === "done" && state.memo) setMemoOpen(true);
  }, [state.run, state.memo]);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        const body = (await res.json()) as { ok?: boolean };
        if (alive) setCore(body.ok ? "online" : "offline");
      } catch {
        if (alive) setCore("offline");
      }
    };
    void check();
    const t = setInterval(check, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const running = state.run === "running";
  const runLabel = running ? "running…" : state.run === "done" ? "↻ replay" : "▶ run campaign";

  return (
    <main className="war-room">
      <header className="wr-header">
        <div className="wr-brand">
          <span className="wr-kicker">Multi-agent due diligence · shared memory · arango-agentic-memory</span>
          <h1 className="wr-title">The Due-Diligence Room</h1>
        </div>
        <div className="wr-case">
          <span className="wr-q">Should we invest in <b>Northwind Robotics</b>?</span>
          {state.memo && (
            <button
              className={`wr-verdict wr-verdict-${RECOMMENDATION[state.memo.recommendation].tone}`}
              onClick={() => setMemoOpen(true)}
            >
              <span className="wr-verdict-dot" />
              {RECOMMENDATION[state.memo.recommendation].label}
              <span className="wr-verdict-more">· view memo</span>
            </button>
          )}
          {core === "online" && (
            <>
              <button
                className="wr-reset"
                onClick={handleReset}
                disabled={running || resetting}
                title={`Clear ${ROOM_ID}'s shared memory so a live run starts clean`}
              >
                {resetting ? "resetting…" : "⌫ reset room"}
              </button>
              <label className="wr-live" title="Run the real agents against the core (falls back to canned if no key)">
                <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} disabled={running} />
                live
              </label>
            </>
          )}
          <button
            className="wr-run"
            onClick={() => start(live ? { canned: false, roomId: ROOM_ID } : { canned: true })}
            disabled={running}
          >
            {runLabel}
          </button>
          <span className={`wr-core wr-core-${core === "online" ? "ok" : core === "offline" ? "down" : "wait"}`}>
            <span className="wr-dot" /> {core === "online" ? "core online" : core === "offline" ? "canned" : "…"}
          </span>
        </div>
      </header>

      <Pipeline state={state} />
      <Narration state={state} />

      <section className="wr-main">
        <aside className="wr-side">
          <AgentRail state={state} />
        </aside>
        <div className="wr-stage">
          <div className="wr-stage-head">
            <span className="wr-stage-title">Evidence graph</span>
            <span className="wr-stage-sub">what the deal team knows — entities, relationships, belief</span>
          </div>
          <EvidenceGraph
            canned={!live}
            roomId={live ? ROOM_ID : undefined}
            refreshKey={`${state.run}:${resetNonce}`}
            activeDispute={active === null ? null : (state.disputes[active] ?? null)}
          />
        </div>
        <aside className="wr-right">
          <ContradictionFeed
            disputes={state.disputes}
            activeIndex={active}
            onHover={setActive}
            running={state.run === "running"}
          />
        </aside>
      </section>

      {memoOpen && state.memo && <MemoPanel memo={state.memo} onClose={() => setMemoOpen(false)} />}
    </main>
  );
}
