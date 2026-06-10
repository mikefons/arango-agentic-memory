"use client";

import { useEffect, useState } from "react";

type State = "checking" | "ok" | "down";

export function HealthStatus() {
  const [state, setState] = useState<State>("checking");

  useEffect(() => {
    let alive = true;
    const ping = () =>
      fetch("/api/health")
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((d: { ok?: boolean }) => alive && setState(d.ok ? "ok" : "down"))
        .catch(() => alive && setState("down"));
    ping();
    const id = setInterval(ping, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const label = state === "ok" ? "core online" : state === "down" ? "core offline" : "connecting…";
  return (
    <span className={`live ${state === "ok" ? "ok" : state === "down" ? "down" : ""}`}>
      <span className="dot" />
      {label}
    </span>
  );
}
