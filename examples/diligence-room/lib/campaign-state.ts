/**
 * War-room state reducer (DR-3c) — folds the campaign SSE events into the state the rail,
 * pipeline, feed, and memo render. Pure and exhaustively testable; the hook (useCampaign) just
 * pipes EventSource messages through `reduceCampaign`.
 */

import type { CampaignEvent } from "./room-state";
import type { Dispute } from "./agents/redteam";
import type { Memo } from "./agents/synthesis";
import type { RoleId } from "./agents-meta";

export type Phase =
  | "specialist:financial"
  | "specialist:legal"
  | "specialist:technical"
  | "specialist:market"
  | "flush:specialists"
  | "consolidate"
  | "redteam"
  | "flush:redteam"
  | "synthesis";

/** Canonical phase order — used to derive which phase is "running" (the one after the last done). */
export const PHASES: Phase[] = [
  "specialist:financial",
  "specialist:legal",
  "specialist:technical",
  "specialist:market",
  "flush:specialists",
  "consolidate",
  "redteam",
  "flush:redteam",
  "synthesis",
];

/** Human labels for the pipeline chips. */
export const PHASE_LABELS: Record<Phase, string> = {
  "specialist:financial": "Financial",
  "specialist:legal": "Legal",
  "specialist:technical": "Technical",
  "specialist:market": "Market",
  "flush:specialists": "Flush",
  consolidate: "Consolidate",
  redteam: "Red-team",
  "flush:redteam": "Flush",
  synthesis: "Synthesis",
};

export type StepStatus = "pending" | "running" | "done" | "error";
export type RunStatus = "idle" | "running" | "done" | "error";

export interface AgentState {
  status: "idle" | "running" | "done" | "error";
  /** Claims written (specialists) / disputes found (red-team) / findings (synthesis). */
  count: number;
}

export interface WarState {
  run: RunStatus;
  phases: Record<Phase, StepStatus>;
  agents: Record<RoleId, AgentState>;
  disputes: Dispute[];
  memo?: Memo;
}

/** Map a phase name to the agent it belongs to (flush/consolidate belong to no agent). */
export function phaseAgent(phase: string): RoleId | null {
  if (phase.startsWith("specialist:")) return phase.slice("specialist:".length) as RoleId;
  if (phase === "redteam") return "redteam";
  if (phase === "synthesis") return "synthesis";
  return null;
}

const ROLE_IDS: RoleId[] = ["financial", "legal", "technical", "market", "redteam", "synthesis"];

export function initialWarState(): WarState {
  return {
    run: "idle",
    phases: Object.fromEntries(PHASES.map((p) => [p, "pending"])) as Record<Phase, StepStatus>,
    agents: Object.fromEntries(ROLE_IDS.map((r) => [r, { status: "idle", count: 0 }])) as Record<
      RoleId,
      AgentState
    >,
    disputes: [],
  };
}

/** Parse the leading integer out of a step detail like "5 claim(s)" → 5. */
function countFromDetail(detail?: string): number {
  const m = detail?.match(/^(\d+)/);
  return m ? Number(m[1]) : 0;
}

/** Fold one SSE event into the state (returns a new object). */
export function reduceCampaign(state: WarState, ev: CampaignEvent): WarState {
  switch (ev.type) {
    case "step": {
      const phase = ev.step.name as Phase;
      if (!(phase in state.phases)) return state;
      const phases = { ...state.phases, [phase]: ev.step.status === "error" ? "error" : "done" };
      // The first still-pending phase becomes "running" (the live cursor).
      const nextPending = PHASES.find((p) => phases[p] === "pending");
      if (nextPending) phases[nextPending] = "running";

      const agents = { ...state.agents };
      const role = phaseAgent(phase);
      if (role) {
        agents[role] = {
          status: ev.step.status === "error" ? "error" : "done",
          count: countFromDetail(ev.step.detail) || agents[role].count,
        };
      }
      // Mark the running phase's agent (if any) as running.
      if (nextPending) {
        const nextRole = phaseAgent(nextPending);
        if (nextRole && agents[nextRole].status === "idle") {
          agents[nextRole] = { ...agents[nextRole], status: "running" };
        }
      }
      return { ...state, run: "running", phases, agents };
    }
    case "disputes":
      return {
        ...state,
        disputes: ev.disputes,
        agents: { ...state.agents, redteam: { ...state.agents.redteam, count: ev.disputes.length } },
      };
    case "memo":
      return {
        ...state,
        memo: ev.memo,
        agents: {
          ...state.agents,
          synthesis: { status: "done", count: ev.memo.findings.length },
        },
      };
    case "done":
      return { ...state, run: ev.ok ? "done" : "error" };
    default:
      return state;
  }
}
