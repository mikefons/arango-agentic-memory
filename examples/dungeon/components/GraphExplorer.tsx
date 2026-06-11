"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  MarkerType,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import ELK from "elkjs/lib/elk.bundled.js";
import { EntityNode, type EntityFlowNode } from "./EntityNode";
import {
  filterGraph,
  relationshipKinds,
  searchMatches,
  type GraphNodeRaw,
  type MemoryGraph,
} from "@/lib/explorer";

const nodeTypes = { entity: EntityNode };
const elk = new ELK();

const ELK_OPTS = {
  "elk.algorithm": "org.eclipse.elk.force",
  "elk.spacing.nodeNode": "70",
  "elk.force.repulsivePower": "1",
};

async function layoutWithElk(
  nodes: GraphNodeRaw[],
  edges: { source: string; target: string }[],
): Promise<Record<string, { x: number; y: number }>> {
  if (nodes.length === 0) return {};
  const graph = {
    id: "root",
    layoutOptions: ELK_OPTS,
    children: nodes.map((n) => ({ id: n.id, width: 150, height: 40 })),
    edges: edges.map((e, i) => ({ id: `e${i}`, sources: [e.source], targets: [e.target] })),
  };
  const res = await elk.layout(graph);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const c of res.children ?? []) pos[c.id] = { x: c.x ?? 0, y: c.y ?? 0 };
  return pos;
}

function useTheme(): "dark" | "light" {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    const read = () =>
      setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

function makeEdge(e: MemoryGraph["edges"][number], i: number): Edge {
  const superseding = e.kind === "supersedes";
  return {
    id: `edge-${i}`,
    source: e.source,
    target: e.target,
    label: e.relationship === "associated_with" ? undefined : e.relationship,
    animated: superseding,
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    className: superseding ? "gx-edge supersede" : "gx-edge relate",
  };
}

function Inner() {
  const theme = useTheme();
  const { fitView, getNode, setCenter } = useReactFlow();

  const [raw, setRaw] = useState<MemoryGraph>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const [showSuperseded, setShowSuperseded] = useState(true);
  // Track only the relationship kinds the user *disabled* — so kinds that appear
  // later (e.g. "supersedes" after a lie is caught) are visible by default.
  const [disabled, setDisabled] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<GraphNodeRaw | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<EntityFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const nodesInitialized = useNodesInitialized();
  const [needsFit, setNeedsFit] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetch("/api/memory-graph")
      .then((r) => (r.ok ? r.json() : { nodes: [], edges: [] }))
      .then((g: MemoryGraph) => setRaw(g))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const allKinds = useMemo(() => relationshipKinds(raw), [raw]);
  const enabled = useMemo(
    () => new Set(allKinds.filter((k) => !disabled.has(k))),
    [allKinds, disabled],
  );
  const filtered = useMemo(
    () => filterGraph(raw, { showSuperseded, relationships: enabled }),
    [raw, showSuperseded, enabled],
  );
  const matches = useMemo(() => searchMatches(filtered.nodes, query), [filtered.nodes, query]);

  const structureKey = useMemo(
    () =>
      filtered.nodes.map((n) => n.id).join(",") +
      "|" +
      filtered.edges.map((e) => `${e.source}>${e.target}:${e.relationship}`).join(","),
    [filtered],
  );

  // relayout when the graph structure changes, then fit once nodes have rendered
  useEffect(() => {
    let alive = true;
    (async () => {
      const pos = await layoutWithElk(filtered.nodes, filtered.edges);
      if (!alive) return;
      setNodes(
        filtered.nodes.map((n) => ({
          id: n.id,
          type: "entity" as const,
          position: pos[n.id] ?? { x: 0, y: 0 },
          data: { node: n, highlighted: false, selected: false },
        })),
      );
      setEdges(filtered.edges.map(makeEdge));
      // fit once the (custom) nodes are actually measured — see the effect below
      setNeedsFit(true);
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureKey]);

  // React Flow can only fit-to-view after the custom nodes have been measured.
  useEffect(() => {
    if (nodesInitialized && needsFit && nodes.length > 0) {
      fitView({ padding: 0.2, duration: 400 });
      setNeedsFit(false);
    }
  }, [nodesInitialized, needsFit, nodes.length, fitView]);

  useEffect(() => {
    setNodes((nds) =>
      nds.map((n) => ({
        ...n,
        data: { ...n.data, highlighted: matches.has(n.id), selected: selected?.id === n.id },
      })),
    );
  }, [matches, selected, setNodes]);

  useEffect(() => {
    if (!query) return;
    const first = filtered.nodes.find((n) => matches.has(n.id));
    const node = first ? getNode(first.id) : undefined;
    if (node) setCenter(node.position.x, node.position.y, { zoom: 1.1, duration: 500 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const toggleRel = (k: string) =>
    setDisabled((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const empty = !loading && filtered.nodes.length === 0;

  return (
    <div className="gx-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        colorMode={theme}
        onNodeClick={(_, n) => setSelected((n.data as { node: GraphNodeRaw }).node)}
        onPaneClick={() => setSelected(null)}
        fitView
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeStrokeWidth={2} />
      </ReactFlow>

      <div className="gx-controls">
        <div className="gx-actions">
          <button className="gx-btn" onClick={refresh} disabled={loading}>
            {loading ? "loading…" : "↻ refresh"}
          </button>
          <button className="gx-btn" onClick={() => fitView({ padding: 0.25, duration: 400 })}>
            ⊹ re-center
          </button>
        </div>
        <input
          className="gx-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search entities…"
          aria-label="Search entities"
        />
        <div className="gx-filters">
          <label className="gx-check">
            <input
              type="checkbox"
              checked={showSuperseded}
              onChange={(e) => setShowSuperseded(e.target.checked)}
            />
            show superseded
          </label>
          {allKinds.map((k) => (
            <label className="gx-check" key={k}>
              <input type="checkbox" checked={!disabled.has(k)} onChange={() => toggleRel(k)} />
              {k}
            </label>
          ))}
        </div>
        <div className="gx-stat">
          {empty
            ? "no entities yet — play a few turns, then refresh"
            : `${filtered.nodes.length} entities · ${filtered.edges.length} relations`}
        </div>
      </div>

      {selected && (
        <div className="gx-inspect">
          <div className="gx-inspect-head">
            <span className="gx-inspect-name">{selected.name}</span>
            <button className="gx-close" onClick={() => setSelected(null)} aria-label="Close">×</button>
          </div>
          <dl className="gx-fields">
            <dt>label</dt><dd>{selected.label}</dd>
            <dt>source</dt><dd>{selected.source ?? "—"}</dd>
            <dt>mentions</dt><dd>{selected.mention_count ?? "—"}</dd>
            <dt>valid time</dt>
            <dd>{selected.valid_time?.slice(0, 10) ?? "—"}{selected.valid_time_explicit ? " (explicit)" : ""}</dd>
            <dt>status</dt>
            <dd className={selected.invalid_at ? "bad" : selected.needs_review ? "warn" : "ok"}>
              {selected.invalid_at ? "superseded" : selected.needs_review ? "needs review" : "valid"}
            </dd>
          </dl>
        </div>
      )}
    </div>
  );
}

export function GraphExplorer() {
  return (
    <ReactFlowProvider>
      <Inner />
    </ReactFlowProvider>
  );
}
