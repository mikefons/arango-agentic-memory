import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { GraphViewNode } from "@/lib/room-state";
import { beliefBorderAlpha, beliefPct, communityHue } from "@/lib/graph-viz";

export interface EvidenceNodeData extends Record<string, unknown> {
  node: GraphViewNode;
  dimmed: boolean;
  /** In the cluster of the active contradiction — gets an accent ring. */
  active?: boolean;
}

export type EvidenceFlowNode = Node<EvidenceNodeData, "evidence">;

/** One entity in the evidence graph: type tag, name, community accent, belief meter. */
export function EvidenceNode({ data }: NodeProps<EvidenceFlowNode>) {
  const { node, dimmed, active } = data;
  const hue = communityHue(node.community);
  return (
    <div
      className={`ev-node${dimmed ? " ev-dimmed" : ""}${active ? " ev-active" : ""}`}
      style={{
        borderColor: active ? hue : hexAlpha(hue, beliefBorderAlpha(node.belief)),
        // faint community wash so clusters read as groups
        background: `linear-gradient(180deg, ${hexAlpha(hue, active ? 0.22 : 0.1)}, transparent)`,
        boxShadow: active ? `0 0 0 2px ${hexAlpha(hue, 0.85)}, 0 4px 18px ${hexAlpha(hue, 0.4)}` : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} className="ev-handle" />
      <span className="ev-type" style={{ color: hue }}>
        {node.label}
      </span>
      <span className="ev-name" title={node.name}>
        {node.name}
      </span>
      <span className="ev-belief" title={`belief ${beliefPct(node.belief)}%`}>
        <span className="ev-belief-fill" style={{ width: `${beliefPct(node.belief)}%`, background: hue }} />
      </span>
      <Handle type="source" position={Position.Bottom} className="ev-handle" />
    </div>
  );
}

/** #rrggbb + 0..1 alpha → rgba() string. */
function hexAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(2)})`;
}
