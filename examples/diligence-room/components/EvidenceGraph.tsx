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
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import ELK from "elkjs/lib/elk.bundled.js";
import { EvidenceNode, type EvidenceFlowNode } from "./EvidenceNode";
import { NODE_HEIGHT, communityHue, nodeWidth } from "@/lib/graph-viz";
import type { GraphView, GraphViewNode } from "@/lib/room-state";

const nodeTypes = { evidence: EvidenceNode };
const elk = new ELK();

const ELK_OPTS = {
  // Stress gives a compact, evenly-spread layout for knowledge graphs (force sprawls).
  "elk.algorithm": "org.eclipse.elk.stress",
  "org.eclipse.elk.stress.desiredEdgeLength": "150",
  "elk.spacing.nodeNode": "40",
};

async function layout(view: GraphView): Promise<Record<string, { x: number; y: number }>> {
  if (view.nodes.length === 0) return {};
  const g = {
    id: "root",
    layoutOptions: ELK_OPTS,
    children: view.nodes.map((n) => ({ id: n.id, width: nodeWidth(n.salience), height: NODE_HEIGHT })),
    edges: view.edges.map((e, i) => ({ id: `e${i}`, sources: [e.source], targets: [e.target] })),
  };
  const res = await elk.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const c of res.children ?? []) pos[c.id] = { x: c.x ?? 0, y: c.y ?? 0 };
  return pos;
}

function Inner({ roomId, canned }: { roomId?: string; canned: boolean }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<EvidenceFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [view, setView] = useState<GraphView | null>(null);
  const rf = useReactFlow();
  const initialized = useNodesInitialized();

  // Fit once React Flow has measured the custom nodes (fitView needs their real sizes).
  useEffect(() => {
    if (initialized && nodes.length > 0) {
      rf.fitView({ padding: 0.16, duration: 500 });
    }
  }, [initialized, nodes.length, rf]);

  const load = useCallback(async () => {
    const qs = canned ? "canned=1" : `roomId=${encodeURIComponent(roomId ?? "")}`;
    const res = await fetch(`/api/room/graph?${qs}&limit=24`, { cache: "no-store" });
    setView((await res.json()) as GraphView);
  }, [roomId, canned]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!view) return;
    let alive = true;
    void layout(view).then((pos) => {
      if (!alive) return;
      setNodes(
        view.nodes.map((n: GraphViewNode) => ({
          id: n.id,
          type: "evidence",
          position: pos[n.id] ?? { x: 0, y: 0 },
          data: { node: n, dimmed: false },
        })),
      );
      setEdges(
        view.edges.map((e, i) => ({
          id: `e${i}`,
          source: e.source,
          target: e.target,
          animated: false,
          style: { stroke: "#3a4453", strokeWidth: 1, opacity: 0.35 + e.belief * 0.5 },
        })),
      );
    });
    return () => {
      alive = false;
    };
  }, [view, setNodes, setEdges]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.2}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="#1e2430" gap={22} />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        nodeColor={(n) => communityHue((n.data as { node?: GraphViewNode })?.node?.community ?? 0)}
        maskColor="rgba(10,14,20,0.7)"
      />
    </ReactFlow>
  );
}

export function EvidenceGraph({ roomId, canned = true }: { roomId?: string; canned?: boolean }) {
  const legend = useMemo(
    () => [
      { k: "size", label: "size = salience (centrality)" },
      { k: "hue", label: "hue = community (related parties)" },
      { k: "meter", label: "bar = belief (corroboration)" },
    ],
    [],
  );
  return (
    <div className="ev-graph">
      <ReactFlowProvider>
        <Inner roomId={roomId} canned={canned} />
      </ReactFlowProvider>
      <div className="ev-legend">
        {legend.map((l) => (
          <span key={l.k} className="ev-legend-item">
            {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}
