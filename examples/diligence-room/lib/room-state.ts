/**
 * Room-state types + transforms for the War Room UI (DR-3). Pure and testable: turns the
 * core's raw graph into a view-model the dashboard renders, and defines the shapes the data
 * routes return. No network here.
 */

import type { SpecialistId } from "./types";
import type { CampaignStep } from "./campaign";
import type { Dispute } from "./agents/redteam";
import type { Memo } from "./agents/synthesis";

/** The events the War Room consumes over the campaign SSE stream (live or canned). */
export type CampaignEvent =
  | { type: "step"; step: CampaignStep }
  | { type: "disputes"; disputes: Dispute[] }
  | { type: "memo"; memo: Memo }
  | { type: "done"; ok: boolean };

// ── Core graph (as returned by GET /v1/graph) ──────────────────────────────
export interface CoreGraphNode {
  id: string;
  name: string;
  label: string;
  belief?: number;
  centrality?: number;
  community?: number;
  mention_count?: number;
  needs_review?: boolean;
  invalid_at?: string | null;
}

export interface CoreGraphEdge {
  source: string;
  target: string;
  relationship?: string;
  belief?: number;
  weight?: number;
}

export interface CoreGraph {
  nodes: CoreGraphNode[];
  edges: CoreGraphEdge[];
}

// ── View model the dashboard renders ───────────────────────────────────────
export interface GraphViewNode {
  id: string;
  name: string;
  label: string;
  /** 0..1 corroboration-weighted belief (ring/opacity). */
  belief: number;
  /** 0..1 PageRank salience (node size). */
  salience: number;
  /** community index (hue) — related parties share a community. */
  community: number;
}

export interface GraphViewEdge {
  source: string;
  target: string;
  relationship: string;
  belief: number;
}

export interface GraphView {
  nodes: GraphViewNode[];
  edges: GraphViewEdge[];
  communities: number;
}

/**
 * Project the core graph to the view model: drop invalidated (superseded) entities, map
 * centrality→salience, and keep the most salient `limit` nodes (with the edges among them).
 */
export function toGraphView(graph: CoreGraph, opts: { limit?: number } = {}): GraphView {
  const live = graph.nodes.filter((n) => !n.invalid_at);
  const ranked = [...live].sort((a, b) => (b.centrality ?? 0) - (a.centrality ?? 0));
  const kept = opts.limit ? ranked.slice(0, opts.limit) : ranked;
  const keptIds = new Set(kept.map((n) => n.id));

  const nodes: GraphViewNode[] = kept.map((n) => ({
    id: n.id,
    name: n.name,
    label: n.label,
    belief: clamp01(n.belief ?? 0),
    salience: clamp01(n.centrality ?? 0),
    community: n.community ?? 0,
  }));

  const edges: GraphViewEdge[] = graph.edges
    .filter((e) => keptIds.has(e.source) && keptIds.has(e.target))
    .map((e) => ({
      source: e.source,
      target: e.target,
      relationship: e.relationship ?? "relates_to",
      belief: clamp01(e.belief ?? 0),
    }));

  const communities = new Set(nodes.map((n) => n.community)).size;
  return { nodes, edges, communities };
}

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}

// ── Provenance colors — every claim/finding is badged by the agent that wrote it ──
export const AGENT_COLORS: Record<string, string> = {
  financial: "#2f7a3f",
  legal: "#2563a8",
  technical: "#7c3aed",
  market: "#b45309",
  redteam: "#b23b3b",
  synthesis: "#0d9488",
};

export function agentColor(agentId: string): string {
  return AGENT_COLORS[agentId] ?? "#6b6b6b";
}

// ── Claims-by-agent summary (agent rail + feed) ────────────────────────────
export interface ClaimRow {
  agent: string;
  text: string;
}

export interface ClaimsSummary {
  byAgent: Partial<Record<SpecialistId | "redteam" | "synthesis", number>>;
  claims: ClaimRow[];
}

export function summarizeClaims(claims: ClaimRow[]): ClaimsSummary {
  const byAgent: ClaimsSummary["byAgent"] = {};
  for (const c of claims) {
    const key = c.agent as keyof ClaimsSummary["byAgent"];
    byAgent[key] = (byAgent[key] ?? 0) + 1;
  }
  return { byAgent, claims };
}
