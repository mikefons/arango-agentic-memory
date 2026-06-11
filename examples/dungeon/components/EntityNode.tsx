import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { isSuperseded, type GraphNodeRaw } from "@/lib/explorer";

export interface EntityNodeData extends Record<string, unknown> {
  node: GraphNodeRaw;
  highlighted: boolean;
  selected: boolean;
}

export type EntityFlowNode = Node<EntityNodeData, "entity">;

export function EntityNode({ data }: NodeProps<EntityFlowNode>) {
  const { node, highlighted, selected } = data;
  const cls = ["gx-node"];
  if (isSuperseded(node)) cls.push("superseded");
  if (node.needs_review) cls.push("review");
  if (highlighted) cls.push("highlighted");
  if (selected) cls.push("selected");

  return (
    <div className={cls.join(" ")} data-label={node.label}>
      <Handle type="target" position={Position.Top} className="gx-handle" />
      <span className="gx-dot" />
      <span className="gx-name">{node.name}</span>
      <span className="gx-label">{node.label}</span>
      <Handle type="source" position={Position.Bottom} className="gx-handle" />
    </div>
  );
}
