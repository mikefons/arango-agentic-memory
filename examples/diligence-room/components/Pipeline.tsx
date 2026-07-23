import { PHASES, PHASE_LABELS, type StepStatus, type WarState } from "@/lib/campaign-state";

/** The campaign pipeline: one chip per phase, lit by live status (DR-2 step-log via SSE). */
export function Pipeline({ state }: { state: WarState }) {
  return (
    <div className="pipe">
      {PHASES.map((p, i) => (
        <div key={`${p}-${i}`} className="pipe-cell">
          <span className={`pipe-chip pipe-${state.phases[p]}`} title={statusLabel(state.phases[p])}>
            {PHASE_LABELS[p]}
          </span>
          {i < PHASES.length - 1 && <span className="pipe-arrow">›</span>}
        </div>
      ))}
    </div>
  );
}

function statusLabel(s: StepStatus): string {
  return s === "running" ? "in progress" : s;
}
