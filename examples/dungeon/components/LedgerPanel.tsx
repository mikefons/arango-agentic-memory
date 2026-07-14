"use client";

import { useEffect, useState } from "react";
import type { GameState } from "@/lib/tools";
import { guildStats, type GuildSave } from "@/lib/guild";
import { caughtCount, totalCaughtLies } from "@/lib/accuse";
import { ACCUSE_THRESHOLD, traitorNpc } from "@/lib/world";
import type { MemoryGraph } from "@/lib/explorer";

/**
 * Guild Ledger (GUILD.md E-5) — the compounding, cross-expedition view: how many
 * heroes the guild has spent, how much of the keep it has mapped, and how close its
 * accumulated evidence is to naming the traitor. The case meter reads the *persistent*
 * memory graph (`caughtCount` on the traitor), so it reflects exactly when `accuse`
 * will succeed — evidence gathered by heroes who are long dead.
 */
export function LedgerPanel({ game, guild }: { game: GameState; guild: GuildSave }) {
  const [graph, setGraph] = useState<MemoryGraph>({ nodes: [], edges: [] });

  // Refetch when a lie is caught (caughtClaims grows) or a new hero descends.
  useEffect(() => {
    fetch("/api/memory-graph")
      .then((r) => (r.ok ? r.json() : { nodes: [], edges: [] }))
      .then((g: MemoryGraph) => setGraph(g))
      .catch(() => undefined);
  }, [game.caughtClaims.length, game.expedition]);

  const stats = guildStats(guild, {
    expeditions: game.expedition,
    liesCaught: totalCaughtLies(graph),
    caught: caughtCount(traitorNpc(), graph),
    needed: ACCUSE_THRESHOLD,
  });
  const c = stats.case;

  return (
    <div className="section guild-ledger">
      <h3>Guild Ledger <span className="count">exp {stats.expeditions}</span></h3>

      <div className="guild-grid">
        <Stat label="expeditions" value={stats.expeditions} />
        <Stat label="heroes lost" value={stats.heroesLost} />
        <Stat label="map filled" value={`${stats.mapFillPct}%`} sub={`${stats.roomsSeen}/${stats.roomsTotal}`} />
        <Stat label="claims heard" value={stats.claimsHeard} />
      </div>

      <div className="case-board">
        <div className="case-head">
          <span className="case-title">The case against {traitorNpc().name}</span>
          <span className="case-num">{c.caught}/{c.needed}</span>
        </div>
        <div className={`meter case${c.solved ? " high" : ""}`}>
          <i style={{ width: `${c.pct}%` }} />
        </div>
        <div className="case-foot">
          {c.solved
            ? "enough proof — accuse the traitor to close the case"
            : `${c.needed - c.caught} more of their lies to expose · evidence persists across heroes`}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div className="guild-stat">
      <div className="gs-val">
        {value}
        {sub && <span className="gs-sub"> {sub}</span>}
      </div>
      <div className="gs-label">{label}</div>
    </div>
  );
}
