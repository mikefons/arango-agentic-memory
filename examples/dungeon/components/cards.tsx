/** Generative-UI cards rendered from tool outputs (3.5c-2). */

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

export function RoomSceneCard({ tool, view }: { tool: "look" | "move"; view: RoomView }) {
  const hue = hueOf(view.name);
  const art = {
    background:
      `radial-gradient(120% 90% at 28% 8%, hsl(${hue} 55% 22%), transparent 55%),` +
      `radial-gradient(95% 120% at 86% 92%, hsl(${(hue + 70) % 360} 45% 15%), transparent 60%),` +
      `linear-gradient(160deg, #161019, #0a0a0f 72%)`,
  };
  return (
    <div className="card">
      <div className="card-head">
        <span className="tool">tool · <b>{tool}</b></span>
        <span className="state">resolved</span>
      </div>
      <div className="scene-art" style={art}>
        <span className="scene-cap">
          <span className="spark">✦</span> {view.name ?? "the dark"} · stored to graph
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
