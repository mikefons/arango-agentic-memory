"use client";

import { useEffect, useState } from "react";
import { EvidenceGraph } from "@/components/EvidenceGraph";

type CoreState = "checking" | "online" | "offline";

export default function WarRoom() {
  const [core, setCore] = useState<CoreState>("checking");

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

  return (
    <main className="war-room">
      <header className="wr-header">
        <div className="wr-brand">
          <span className="wr-kicker">arango-agentic-memory</span>
          <h1 className="wr-title">The Due-Diligence Room</h1>
        </div>
        <div className="wr-case">
          <span className="wr-q">Should we invest in <b>Northwind Robotics</b>?</span>
          <span className={`wr-core wr-core-${core === "online" ? "ok" : core === "offline" ? "down" : "wait"}`}>
            <span className="wr-dot" /> {core === "online" ? "core online" : core === "offline" ? "canned mode" : "…"}
          </span>
        </div>
      </header>

      <section className="wr-stage">
        <div className="wr-stage-head">
          <span className="wr-stage-title">Evidence graph</span>
          <span className="wr-stage-sub">what the deal team knows — entities, relationships, belief</span>
        </div>
        <EvidenceGraph canned />
      </section>
    </main>
  );
}
