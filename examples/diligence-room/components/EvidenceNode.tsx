import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { GraphViewNode } from "@/lib/room-state";
import { beliefBorderAlpha, beliefPct, communityHue } from "@/lib/graph-viz";

export interface EvidenceNodeData extends Record<string, unknown> {
  node: GraphViewNode;
  dimmed: boolean;
}

export type EvidenceFlowNode = Node<EvidenceNodeData, "evidence">;

/** One entity in the evidence graph: type tag, name, community accent, belief meter. */
export function EvidenceNode({ data }: NodeProps<EvidenceFlowNode>) {
  const { node, dimmed } = data;
  const hue = communityHue(node.community);
  return (
    <div
      className={`ev-node${dimmed ? " ev-dimmed" : ""}`}
      style={{
        borderColor: hexAlpha(hue, beliefBorderAlpha(node.belief)),
        // faint community wash so clusters read as groups
        background: `linear-gradient(180deg, ${hexAlpha(hue, 0.1)}, transparent)`,
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
