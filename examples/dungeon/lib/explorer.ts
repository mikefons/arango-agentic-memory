/**
 * Pure transforms for the Graph Explorer (no React Flow import, so unit-testable).
 *
 * The core's `GET /v1/graph` returns the tenant's semantic graph — entities
 * (including superseded ones, carrying `invalid_at`) plus `relates_to`/`Supersedes`
 * edges. Here we filter (by edge relationship + supersession) and search; the
 * React Flow / elk layout mapping lives in the component.
 */

export interface GraphNodeRaw {
  id: string;
  name: string;
  label: string;
  source?: string;
  mention_count?: number;
  valid_time?: string;
  valid_time_explicit?: boolean;
  needs_review?: boolean;
  conflict_with?: string | null;
  invalid_at?: string | null;
  belief?: number;
  centrality?: number;
  community?: number;
}

/**
 * A stable, well-spread hue for a community label (golden-angle around the wheel),
 * mirroring the centrality node-sizing cue. Returns `null` when unlabeled
 * (`undefined` or the `-1` sentinel) so the node keeps its default accent.
 */
export function communityColor(community: number | undefined | null): string | null {
  if (community == null || community < 0) return null;
  return `hsl(${Math.round((community * 137.508) % 360)} 60% 58%)`;
}

export interface GraphEdgeRaw {
  source: string;
  target: string;
  relationship: string;
  kind: "relates_to" | "supersedes";
}

export interface MemoryGraph {
  nodes: GraphNodeRaw[];
  edges: GraphEdgeRaw[];
}

export function isSuperseded(n: GraphNodeRaw): boolean {
  return n.invalid_at != null;
}

/** Distinct relationship labels present in the graph (drives the filter UI). */
export function relationshipKinds(graph: MemoryGraph): string[] {
  return [...new Set(graph.edges.map((e) => e.relationship))].sort();
}

export interface FilterOpts {
  showSuperseded: boolean;
  relationships: Set<string>;
}

/** Apply the supersession toggle + edge-type filter; drop dangling edges. */
export function filterGraph(graph: MemoryGraph, opts: FilterOpts): MemoryGraph {
  const nodes = graph.nodes.filter((n) => opts.showSuperseded || !isSuperseded(n));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter(
    (e) => opts.relationships.has(e.relationship) && ids.has(e.source) && ids.has(e.target),
  );
  return { nodes, edges };
}

/** Node ids whose name contains the query (case-insensitive). */
export function searchMatches(nodes: GraphNodeRaw[], query: string): Set<string> {
  const q = query.trim().toLowerCase();
  if (!q) return new Set();
  return new Set(nodes.filter((n) => n.name.toLowerCase().includes(q)).map((n) => n.id));
}
