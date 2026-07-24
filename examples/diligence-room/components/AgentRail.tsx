import { ROLES } from "@/lib/agents-meta";
import { agentColor } from "@/lib/room-state";
import { PANEL_CAPTIONS } from "@/lib/callouts";
import type { WarState } from "@/lib/campaign-state";

/** The six agents, color-coded by provenance, showing live status + output counts (DR-3c). */
export function AgentRail({ state }: { state: WarState }) {
  return (
    <div className="rail">
      <div className="rail-title">Deal team</div>
      <p className="panel-cap">{PANEL_CAPTIONS.rail}</p>
      {ROLES.map((role) => {
        const a = state.agents[role.id];
        const color = agentColor(role.id);
        return (
          <div key={role.id} className={`agent agent-${a.status}`}>
            <span className="agent-tick" style={{ background: color }} />
            <div className="agent-body">
              <div className="agent-head">
                <span className="agent-name">{role.title}</span>
                <span className="agent-status">{label(a.status, a.count, role.id)}</span>
              </div>
              <div className="agent-blurb">{role.blurb}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function label(status: string, count: number, id: string): string {
  if (status === "idle") return "idle";
  if (status === "running") return "working…";
  if (status === "error") return "error";
  // done
  if (id === "redteam") return `${count} dispute${count === 1 ? "" : "s"}`;
  if (id === "synthesis") return `${count} finding${count === 1 ? "" : "s"}`;
  return `${count} claim${count === 1 ? "" : "s"}`;
}
