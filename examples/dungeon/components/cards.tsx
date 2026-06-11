"use client";

/** Generative-UI cards rendered from tool outputs (3.5c-2 / memory glimpse). */

import { useEffect, useState } from "react";
import { roomMemory, type DungeonGraph, type GraphNode } from "@/lib/graph";

// Cache room → scene-art URL across cards so each room is fetched once.
const sceneCache = new Map<string, string>();

function useSceneArt(room: string | undefined, enabled: boolean): string | null {
  const [url, setUrl] = useState<string | null>(
    enabled && room ? sceneCache.get(room) ?? null : null,
  );
  useEffect(() => {
    if (!enabled || !room) return;
    const hit = sceneCache.get(room);
    if (hit) {
      setUrl(hit);
      return;
    }
    let alive = true;
    fetch(`/api/scene?room=${encodeURIComponent(room)}`)
      .then((r) => (r.ok && r.status !== 204 ? r.json() : null))
      .then((d: { url?: string } | null) => {
        if (alive && d?.url) {
          sceneCache.set(room, d.url);
          setUrl(d.url);
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [room, enabled]);
  return url;
}

export interface RoomView {
  name?: string;
  description?: string;
  exits?: string[];
  items?: string[];
}

const DIR_KEY: Record<string, string> = {
  north: "N", south: "S", east: "E", west: "W", up: "U", down: "D",
};

/** Deterministic, moody hue per room so each scene reads distinct but on-theme. */
function hueOf(name = ""): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
}

function shortName(name: string): string {
  const n = name.replace(/^the\s+/i, "");
  return n.length > 12 ? `${n.slice(0, 11)}…` : n;
}

/** A constellation of what the memory core remembers about this room — the
 *  room node at centre, its graph neighbours orbiting. Replaces the old static
 *  art tint with an honest window into the knowledge graph. */
function MemoryGlimpse({ facts }: { facts: GraphNode[] }) {
  const cx = 140;
  const cy = 60;
  const r = 44;
  return (
    <svg className="glimpse" viewBox="0 0 280 124" preserveAspectRatio="xMidYMid meet">
      {facts.map((f, i) => {
        const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(facts.length, 1);
        const x = cx + r * Math.cos(a);
        const y = cy + r * Math.sin(a);
        return (
          <g key={f.id} className="gfact">
            <line className="glink" x1={cx} y1={cy} x2={x} y2={y} />
            <circle className={`gnode ${f.kind}`} cx={x} cy={y} r={f.kind === "room" ? 4.5 : 3} />
            <text className="glabel" x={x} y={y - 7}>{shortName(f.name)}</text>
          </g>
        );
      })}
      <circle className="gcenter" cx={cx} cy={cy} r={6.5} />
    </svg>
  );
}

export function RoomSceneCard({
  tool,
  view,
  graph,
  sceneArt = false,
}: {
  tool: "look" | "move";
  view: RoomView;
  graph?: DungeonGraph;
  sceneArt?: boolean;
}) {
  const hue = hueOf(view.name);
  const gradient =
    `radial-gradient(120% 90% at 28% 8%, hsl(${hue} 45% 16%), transparent 55%),` +
    `radial-gradient(95% 120% at 86% 92%, hsl(${(hue + 70) % 360} 38% 12%), transparent 60%),` +
    `linear-gradient(160deg, #141019, #0a0a0f 74%)`;
  const sceneUrl = useSceneArt(view.name, sceneArt);
  const art = sceneUrl
    ? { backgroundImage: `url(${sceneUrl})`, backgroundSize: "cover", backgroundPosition: "center" }
    : { background: gradient };
  const mem = graph ? roomMemory(graph, view.name ?? "") : { found: false, facts: [] };
  const remembered = mem.facts.length;
  return (
    <div className="card">
      <div className="card-head">
        <span className="tool">tool · <b>{tool}</b></span>
        <span className="state">resolved</span>
      </div>
      <div className="scene-art" style={art}>
        {sceneUrl && <div className="scene-scrim" />}
        {remembered > 0 && <MemoryGlimpse facts={mem.facts} />}
        <span className="scene-cap">
          <span className="spark">✦</span>{" "}
          {remembered > 0
            ? `remembered here · ${remembered} linked`
            : `${view.name ?? "the dark"} · committing to memory…`}
        </span>
      </div>
      {view.description && <p className="card-desc">{view.description}</p>}
      {(view.exits?.length || view.items?.length) && (
        <div className="exits">
          {view.exits?.map((d) => (
            <span className="chip" key={`x-${d}`}>
              <span className="k">{DIR_KEY[d] ?? "·"}</span> {d}
            </span>
          ))}
          {view.items?.map((it) => (
            <span className="chip item" key={`i-${it}`}>◇ {it}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function PickupNote({ ok, item, reason }: { ok?: boolean; item?: string; reason?: string }) {
  return (
    <div className={`pickup ${ok ? "ok" : "no"}`}>
      <span className="mark">{ok ? "◇" : "✕"}</span>
      {ok ? <>Took the <b>{item}</b> — added to your satchel.</> : reason ?? "Nothing to take."}
    </div>
  );
}

export function ToolSkeleton({ tool }: { tool: string }) {
  return <div className="tool-skel">↳ {tool}…</div>;
}

export interface TalkView {
  npc?: string;
  role?: string;
  claims?: { id: string; text: string }[];
}

export function TalkCard({ view }: { view: TalkView }) {
  return (
    <div className="card talk">
      <div className="card-head">
        <span className="tool">tool · <b>talk</b></span>
        <span className="who">{view.npc}{view.role ? ` · ${view.role}` : ""}</span>
      </div>
      <div className="testimony">
        {(view.claims ?? []).map((c) => (
          <p className="say" key={c.id}>“{c.text}”</p>
        ))}
      </div>
    </div>
  );
}

export interface ConfrontView {
  caught?: boolean;
  npc?: string;
  confession?: string;
  reason?: string;
}

export function ConfrontCard({ view }: { view: ConfrontView }) {
  if (view.caught) {
    return (
      <div className="confront caught">
        <div className="ribbon">▲ contradiction confirmed</div>
        <p className="say">“{view.confession}”</p>
        <div className="resolve">✓ superseded · {view.npc}&apos;s claim invalidated</div>
      </div>
    );
  }
  return (
    <div className="confront held">
      <div className="ribbon held">— they hold firm</div>
      <p className="say">{view.reason}</p>
    </div>
  );
}
