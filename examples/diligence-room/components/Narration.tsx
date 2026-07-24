import { narrate } from "@/lib/narration";
import { agentColor } from "@/lib/room-state";
import type { WarState } from "@/lib/campaign-state";

/**
 * The guided-narration ribbon (DR-3f) — a running commentary that steps a first-time viewer
 * through the campaign and names the memory capability each phase exercises.
 */
export function Narration({ state }: { state: WarState }) {
  const n = narrate(state);
  const tint = n.agent ? agentColor(n.agent) : "var(--accent)";
  return (
    <div className="narr" style={{ borderLeftColor: tint }}>
      <span className="narr-step" style={{ color: tint }}>
        {n.step}/{n.total}
      </span>
      <div className="narr-body">
        <span className="narr-line">{n.line}</span>
        {n.note && <span className="narr-note">{n.note}</span>}
      </div>
    </div>
  );
}
