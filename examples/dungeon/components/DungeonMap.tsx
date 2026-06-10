"use client";

import { useEffect, useMemo, useState } from "react";
import type { DungeonGraph, GraphNode } from "@/lib/graph";

const CX = 140;
const CY = 205;
const R_ROOM = 80;
const R_LORE = 122;
const MAX_LORE = 14;

interface Pos {
  x: number;
  y: number;
}

function ring(items: GraphNode[], radius: number, start = -Math.PI / 2): Record<string, Pos> {
  const out: Record<string, Pos> = {};
  const n = Math.max(items.length, 1);
  items.forEach((node, i) => {
    const a = start + (2 * Math.PI * i) / n;
    out[node.id] = { x: CX + radius * Math.cos(a), y: CY + radius * Math.sin(a) };
  });
  return out;
}

function isHere(name: string, currentRoom: string): boolean {
  const a = name.toLowerCase();
  const b = currentRoom.toLowerCase();
  return a === b || b.includes(a) || a.includes(b);
}

export function DungeonMap({ currentRoom, refreshKey }: { currentRoom: string; refreshKey: number }) {
  const [graph, setGraph] = useState<DungeonGraph>({ nodes: [], edges: [] });

  useEffect(() => {
    let alive = true;
    fetch("/api/graph")
      .then((r) => (r.ok ? r.json() : { nodes: [], edges: [] }))
      .then((g: DungeonGraph) => alive && setGraph(g))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const { rooms, lore, pos } = useMemo(() => {
    const rooms = graph.nodes.filter((n) => n.kind === "room").sort((a, b) => a.name.localeCompare(b.name));
    const lore = graph.nodes
      .filter((n) => n.kind === "lore")
      .sort((a, b) => a.name.localeCompare(b.name))
      .slice(0, MAX_LORE);
    const pos = { ...ring(rooms, R_ROOM), ...ring(lore, R_LORE, -Math.PI / 2 + 0.4) };
    return { rooms, lore, pos };
  }, [graph]);

  const hasMap = rooms.length > 0 || lore.length > 0;

  return (
    <section className="pane map">
      <div className="pane-head">
        <span className="pane-title">Map</span>
        <span className="pane-meta">{rooms.length} rooms · {graph.edges.length} edges</span>
      </div>

      <div className="map-body">
        {!hasMap ? (
          <div className="placeholder">explore the keep to map it · the graph is memory</div>
        ) : (
          <svg className="map-svg" viewBox="0 0 280 410" preserveAspectRatio="xMidYMid meet">
            {graph.edges.map((e, i) => {
              const a = pos[e.source];
              const b = pos[e.target];
              if (!a || !b) return null;
              return <line className="edge" key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
            })}
            {lore.map((n) => {
              const p = pos[n.id];
              return (
                <g className="node lore" key={n.id} transform={`translate(${p.x},${p.y})`}>
                  <title>{n.name}</title>
                  <circle className="ring" r={6} />
                  <circle className="core" r={1.8} />
                </g>
              );
            })}
            {rooms.map((n) => {
              const p = pos[n.id];
              const here = isHere(n.name, currentRoom);
              return (
                <g className={`node room${here ? " here" : ""}`} key={n.id} transform={`translate(${p.x},${p.y})`}>
                  <title>{n.name}</title>
                  <circle className="ring" r={here ? 15 : 12} />
                  <circle className="core" r={here ? 3.6 : 2.8} />
                  <text y={-20}>{n.name.replace(/^the\s+/i, "")}</text>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      <div className="map-legend">
        <div className="legend-row"><span className="swatch here" /> you are here</div>
        <div className="legend-row"><span className="swatch room" /> discovered room</div>
        <div className="legend-row"><span className="swatch lore" /> remembered detail</div>
      </div>
    </section>
  );
}
