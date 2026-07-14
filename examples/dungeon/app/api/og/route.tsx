import { ImageResponse } from "next/og";
import { memoryGraph } from "@/lib/core";

// Shareable "Dungeon Run" card (1200×630). Stats come from query params (client
// game state) + a live count of the tenant's memory graph from the core.
export const runtime = "nodejs";

const EMBER = "#f0a437";
const LIE = "#ff5c5c";
const TRUTH = "#46c9a0";

function stat(label: string, value: number | string, color = EMBER) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "22px 30px",
        border: "1px solid rgba(255,255,255,0.12)",
        borderRadius: 16,
        background: "#0e0e0e",
      }}
    >
      <div style={{ display: "flex", fontSize: 58, fontWeight: 700, color }}>{value}</div>
      <div style={{ display: "flex", fontSize: 19, color: "#8f8f8f", letterSpacing: 1 }}>{label}</div>
    </div>
  );
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const room = searchParams.get("room") ?? "Ashfall Keep";
  const items = Number(searchParams.get("items") ?? 0) || 0;
  const lies = Number(searchParams.get("lies") ?? 0) || 0;
  const hero = searchParams.get("hero") ?? "";
  const glyph = searchParams.get("glyph") ?? "";
  const expedition = Number(searchParams.get("expedition") ?? 0) || 0;

  let entities = 0;
  let relations = 0;
  try {
    const g = await memoryGraph("dungeon-player");
    entities = g.nodes.length;
    relations = g.edges.length;
  } catch {
    /* core unreachable → zeros */
  }

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          background: "#0a0a0a",
          color: "#ededed",
          padding: "64px 72px",
          fontFamily: "sans-serif",
          position: "relative",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: -160,
            left: 360,
            width: 700,
            height: 460,
            background: "radial-gradient(circle, rgba(240,164,55,0.16), rgba(240,164,55,0) 70%)",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span style={{ color: EMBER, fontSize: 34 }}>▲</span>
          <span style={{ letterSpacing: 8, fontSize: 22, color: "#8f8f8f" }}>THE GUILD</span>
        </div>

        <div style={{ display: "flex", marginTop: 30, fontSize: 64, fontWeight: 700 }}>
          {hero ? `${glyph} ${hero}` : "A Run Through Ashfall Keep"}
        </div>
        <div style={{ display: "flex", marginTop: 14, fontSize: 28, color: "#c9c9c9" }}>
          {expedition ? `expedition ${expedition} · last seen in ${room}` : `last seen in ${room}`}
        </div>

        <div style={{ display: "flex", gap: 24, marginTop: "auto" }}>
          {stat("entities remembered", entities)}
          {stat("relations", relations, TRUTH)}
          {stat("items found", items)}
          {stat("lies caught", lies, LIE)}
        </div>

        <div style={{ display: "flex", marginTop: 36, fontSize: 22, color: "#5a5a5a", letterSpacing: 2 }}>
          the world persists · the NPCs lie · built on ArangoDB agentic memory
        </div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
