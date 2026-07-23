"use client";

import { useEffect, useState } from "react";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { AgentRail } from "@/components/AgentRail";
import { Pipeline } from "@/components/Pipeline";
import { useCampaign } from "@/components/useCampaign";

type CoreState = "checking" | "online" | "offline";

export default function WarRoom() {
  const [core, setCore] = useState<CoreState>("checking");
  const { state, start } = useCampaign();

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
          <span className="wr-kicker">arango-agentic-memory</span>
          <h1 className="wr-title">The Due-Diligence Room</h1>
        </div>
        <div className="wr-case">
          <span className="wr-q">Should we invest in <b>Northwind Robotics</b>?</span>
          <button className="wr-run" onClick={() => start({ canned: true })} disabled={running}>
            {runLabel}
          </button>
          <span className={`wr-core wr-core-${core === "online" ? "ok" : core === "offline" ? "down" : "wait"}`}>
            <span className="wr-dot" /> {core === "online" ? "core online" : core === "offline" ? "canned" : "…"}
          </span>
        </div>
      </header>

      <Pipeline state={state} />

      <section className="wr-main">
        <aside className="wr-side">
          <AgentRail state={state} />
        </aside>
        <div className="wr-stage">
          <div className="wr-stage-head">
            <span className="wr-stage-title">Evidence graph</span>
            <span className="wr-stage-sub">what the deal team knows — entities, relationships, belief</span>
          </div>
          <EvidenceGraph canned refreshKey={state.run} />
        </div>
      </section>
    </main>
  );
}
