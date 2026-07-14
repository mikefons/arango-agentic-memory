/**
 * Guild meta-progression (GUILD.md E-5) — what the guild accumulates *across*
 * expeditions, distinct from a single hero's per-run GameState.
 *
 * A hero's knowledge dies with them; the guild's does not. `roomsSeen` and
 * `claimsHeard` are the union over every hero ever sent (guild cartography +
 * testimony), and the case meter tracks caught lies toward the accusation
 * threshold — all persisted client-side alongside the game save. Pure +
 * unit-testable (no core, no DOM).
 */

import { DUNGEON } from "./world";

export interface GuildSave {
  heroesLost: number; // heroes that died — torch out, perished, or fled
  roomsSeen: string[]; // union of room ids any hero has stood in
  claimsHeard: string[]; // union of claim ids any hero has heard
}

export const EMPTY_GUILD: GuildSave = { heroesLost: 0, roomsSeen: [], claimsHeard: [] };

export const ROOMS_TOTAL = Object.keys(DUNGEON).length;

/** Fold a hero's progress into the guild's persistent knowledge (union). Returns the
 *  same object when nothing is new, so callers can skip a needless persist. */
export function absorb(save: GuildSave, roomIds: string[], heardClaims: string[]): GuildSave {
  const roomsSeen = [...new Set([...save.roomsSeen, ...roomIds])];
  const claims = [...new Set([...save.claimsHeard, ...heardClaims])];
  if (roomsSeen.length === save.roomsSeen.length && claims.length === save.claimsHeard.length) {
    return save;
  }
  return { ...save, roomsSeen, claimsHeard: claims };
}

/** Record a hero's death — the guild endures and recruits another. */
export function loseHero(save: GuildSave): GuildSave {
  return { ...save, heroesLost: save.heroesLost + 1 };
}

export interface CaseProgress {
  caught: number;
  needed: number;
  pct: number;
  solved: boolean;
}

/** Progress toward accusing the traitor, clamped to [0, needed]. */
export function caseProgress(caught: number, needed: number): CaseProgress {
  const c = Math.max(0, caught);
  return {
    caught: c,
    needed,
    pct: needed > 0 ? Math.min(100, Math.round((c / needed) * 100)) : 0,
    solved: c >= needed,
  };
}

export interface GuildStats {
  expeditions: number;
  heroesLost: number;
  roomsSeen: number;
  roomsTotal: number;
  mapFillPct: number;
  claimsHeard: number;
  liesCaught: number;
  case: CaseProgress;
}

/** Compose the ledger stats. `expeditions` is the live hero number; graph-derived
 *  counts (`liesCaught`, `caught`) come from the persistent memory graph. */
export function guildStats(
  save: GuildSave,
  opts: { expeditions: number; liesCaught: number; caught: number; needed: number },
): GuildStats {
  return {
    expeditions: opts.expeditions,
    heroesLost: save.heroesLost,
    roomsSeen: save.roomsSeen.length,
    roomsTotal: ROOMS_TOTAL,
    mapFillPct: ROOMS_TOTAL > 0 ? Math.round((save.roomsSeen.length / ROOMS_TOTAL) * 100) : 0,
    claimsHeard: save.claimsHeard.length,
    liesCaught: opts.liesCaught,
    case: caseProgress(opts.caught, opts.needed),
  };
}

// ── Map cartography (E-5) ─────────────────────────────────────
// The map shows guild knowledge (every room ever discovered, in memory) vs the
// current hero's context (the rooms *this* hero has actually stood in). The
// distinction — memory vs context — rendered as two tones.

export type RoomTint = "here" | "visited" | "guild";

/** Fuzzy room-name match (memory-graph node names vs dungeon room names). */
export function roomMatches(a: string, b: string): boolean {
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  return x === y || x.includes(y) || y.includes(x);
}

/** Where a mapped room sits: the hero's current room, one they've visited this run,
 *  or one only the guild remembers (a prior hero mapped it; this hero hasn't been). */
export function roomTint(nodeName: string, currentRoom: string, visited: string[]): RoomTint {
  if (roomMatches(nodeName, currentRoom)) return "here";
  if (visited.some((v) => roomMatches(nodeName, v))) return "visited";
  return "guild";
}
