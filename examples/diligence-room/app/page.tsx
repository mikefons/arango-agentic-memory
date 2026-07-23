"use client";

import { useEffect, useState } from "react";

type CoreState = "checking" | "online" | "offline";

export default function Home() {
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

  const dot = core === "online" ? "ok" : core === "offline" ? "down" : "";
  const label =
    core === "checking" ? "checking core…" : core === "online" ? "core online" : "core offline";

  return (
    <main className="wrap">
      <div className="kicker">arango-agentic-memory · demo</div>
      <h1>The Due-Diligence Room</h1>
      <p className="lede">
        Specialist agents interrogate a target company&rsquo;s data room, disagree, correct
        each other over time, and hand off to a red-team and a synthesizer — reasoning over one
        shared, bi-temporal, contradiction-aware memory. This is the DR-0a scaffold: the app is
        wired to the core and ready for the specialists.
      </p>

      <div className="status">
        <span className={`dot ${dot}`} />
        {label}
      </div>

      <div>
        <button className="cta" disabled title="Coming in DR-2/DR-3">
          Open a Room →
        </button>
      </div>
    </main>
  );
}
