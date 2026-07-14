/**
 * Static seed dungeon — reference data the tools validate against (3.5c-1).
 *
 * The *durable* world (what the DM recalls across sessions) lives in the memory
 * core as entities/relations; this module is just the fixed geography + items a
 * playthrough explores. Pure helpers here are unit-tested without a model or core.
 */

export type Dir = "north" | "south" | "east" | "west" | "up" | "down";

export const DIRECTIONS: Dir[] = ["north", "south", "east", "west", "up", "down"];

export interface Room {
  id: string;
  name: string;
  description: string;
  exits: Partial<Record<Dir, string>>;
  items: string[];
}

export const START_ROOM = "gatehouse";

export const DUNGEON: Record<string, Room> = {
  gatehouse: {
    id: "gatehouse",
    name: "The Gatehouse",
    description:
      "A soot-stained archway opens onto a ruined keep. A cold draft rises from a stairwell descending into the dark.",
    exits: { north: "barracks", down: "cistern" },
    items: ["rusted torch"],
  },
  barracks: {
    id: "barracks",
    name: "The Barracks",
    description:
      "Rotted bunks line the walls. The gaoler Veld keeps watch here, both hands where you can see them.",
    exits: { south: "gatehouse", east: "smithy" },
    items: ["iron rations"],
  },
  smithy: {
    id: "smithy",
    name: "The Smithy",
    description:
      "A cold forge, anvils green with age. Tools hang in neat rows — too neat for a place abandoned for years.",
    exits: { west: "barracks", down: "archive" },
    items: ["smith's hammer"],
  },
  cistern: {
    id: "cistern",
    name: "The Cistern",
    description:
      "Black water laps at a railed walkway. Somewhere below, a sluice gate groans against the current.",
    exits: { up: "gatehouse", north: "vault" },
    items: ["guttering lantern"],
  },
  vault: {
    id: "vault",
    name: "The Drowned Vault",
    description:
      "A half-flooded treasury, its strongboxes burst and barnacled. A warm lantern sits on a ledge — someone was here recently.",
    exits: { south: "cistern", north: "archive" },
    items: ["brass key", "waterlogged ledger"],
  },
  archive: {
    id: "archive",
    name: "The Archive",
    description:
      "Shelves of swollen tomes. The archivist Mara works by candlelight, cross-referencing a ledger dated only last month.",
    exits: { up: "smithy", south: "vault", east: "countinghouse" },
    items: ["sealed dispatch"],
  },
  countinghouse: {
    id: "countinghouse",
    name: "The Counting House",
    description:
      "Strongbox-keys hang on hooks and coin is stacked in neat towers. The merchant Saro tallies a fortune he shouldn't have, and smiles a beat too quickly when you enter.",
    exits: { west: "archive" },
    items: [],
  },
};

/** The destination room id for an exit, or null if there's no such exit. */
export function resolveMove(roomId: string, direction: Dir): string | null {
  return DUNGEON[roomId]?.exits[direction] ?? null;
}

/** The canonical item name if it's present in the room (case-insensitive), else null. */
export function itemInRoom(roomId: string, item: string): string | null {
  const needle = item.trim().toLowerCase();
  return DUNGEON[roomId]?.items.find((i) => i.toLowerCase() === needle) ?? null;
}

/** The available exit directions from a room. */
export function exitsOf(roomId: string): Dir[] {
  const room = DUNGEON[roomId];
  return room ? (Object.keys(room.exits) as Dir[]) : [];
}

export function getRoom(roomId: string): Room {
  return DUNGEON[roomId] ?? DUNGEON[START_ROOM];
}

// ── NPCs & the lie engine (3.5c-3) ─────────────────────────
//
// Each claim is testimony the NPC offers. A *lie* is exposable once the player
// holds the refuting item OR has heard the contradicting claim — then it can be
// confronted, which supersedes the false fact in the memory core (bi-temporal,
// §12). `subject`/`truth` are canonical entity names minted via the core's seed
// so `confront` has real keys to supersede.

export interface ClaimNeed {
  item?: string; // exposable if this item is in the satchel
  heard?: string; // …or if this claim id has been heard
}

export interface Claim {
  id: string;
  text: string; // what the NPC says (narration source)
  lie: boolean;
  subject: string; // canonical entity name for the claim (seeded)
  refutedBy?: string; // shown in the ledger
  needs?: ClaimNeed; // what makes the lie exposable (omit → always exposable)
  truth?: string; // the corrected fact (the entity that supersedes the lie)
  confession?: string; // the NPC's line when caught
}

export interface Npc {
  id: string;
  name: string;
  role: string;
  room: string;
  baseTrust: number; // 0..100
  claims: Claim[];
  traitor?: boolean; // the arc's culprit — accusable once enough of their lies are exposed (E-4)
}

/** The traitor and how many of their lies the guild must expose to accuse them (E-4). */
export const TRAITOR_ID = "saro";
export const ACCUSE_THRESHOLD = 4;

export const NPCS: Record<string, Npc> = {
  veld: {
    id: "veld",
    name: "Veld",
    role: "gaoler",
    room: "barracks",
    baseTrust: 45,
    claims: [
      {
        id: "veld-key",
        text: "I hold the only key — nobody's set foot in the vault since the flood.",
        lie: true,
        subject: "Veld's Sole Key Claim",
        refutedBy: "the brass key in your satchel · or Mara's freshly-dated ledger",
        needs: { item: "brass key", heard: "mara-ledger" },
        truth: "Vault Entry Record",
        confession: "…Aye. I lied. The merchant Saro paid me to look the other way.",
      },
      {
        id: "veld-drain",
        text: "The storm drain gave out three winters back. That's all that happened here.",
        lie: false,
        subject: "Storm Drain",
      },
    ],
  },
  mara: {
    id: "mara",
    name: "Mara",
    role: "archivist",
    room: "archive",
    baseTrust: 78,
    claims: [
      {
        id: "mara-ledger",
        text: "Odd thing — the vault ledger is dated only last month, not years ago.",
        lie: false,
        subject: "Vault Ledger",
      },
      {
        id: "mara-saro",
        text: "A merchant called Saro came asking after the vault, not long before you.",
        lie: false,
        subject: "Saro",
      },
    ],
  },
  saro: {
    id: "saro",
    name: "Saro",
    role: "merchant",
    room: "countinghouse",
    baseTrust: 60,
    traitor: true,
    claims: [
      {
        id: "saro-vault",
        text: "I never once set foot in that drowned vault — why would I?",
        lie: true,
        subject: "Saro's Vault Denial",
        refutedBy: "Veld's confession that Saro paid him to look away",
        needs: { heard: "veld-key" },
        truth: "Saro at the Vault",
        confession: "…so the gaoler talked. Fine — I was there. But I took only what was owed me.",
      },
      {
        id: "saro-key",
        text: "I have never held a key to any strongbox in my life.",
        lie: true,
        subject: "Saro's Key Denial",
        refutedBy: "the brass key you carry",
        needs: { item: "brass key" },
        truth: "Saro's Stolen Key",
        confession: "That key is a duplicate. The vault was already— no. You have me.",
      },
      {
        id: "saro-forge",
        text: "Those strongboxes were burst by the flood, not by any hand.",
        lie: true,
        subject: "Saro's Flood Story",
        refutedBy: "the smith's hammer, its face marked with box-iron",
        needs: { item: "smith's hammer" },
        truth: "Saro Forced the Boxes",
        confession: "The hammer, then. I pried them. Water doesn't leave dents shaped like a claw.",
      },
      {
        id: "saro-alibi",
        text: "I was trading in the low market all that week — ask anyone.",
        lie: true,
        subject: "Saro's Alibi",
        refutedBy: "the sealed dispatch under his own mark, dated here",
        needs: { item: "sealed dispatch" },
        truth: "Saro's Broken Alibi",
        confession: "The dispatch is dated. I know. I was here. I was always here.",
      },
    ],
  },
};

export interface Evidence {
  inventory: string[];
  heardClaims: string[];
}

export function npcsInRoom(roomId: string): Npc[] {
  return Object.values(NPCS).filter((n) => n.room === roomId);
}

export function getNpc(idOrName: string): Npc | undefined {
  const q = idOrName.trim().toLowerCase();
  return Object.values(NPCS).find((n) => n.id === q || n.name.toLowerCase() === q);
}

/** The arc's traitor (E-4). */
export function traitorNpc(): Npc {
  return NPCS[TRAITOR_ID];
}

/** The room that holds an item (for critical-path reasoning). */
export function roomOfItem(item: string): string | undefined {
  const q = item.toLowerCase();
  return Object.values(DUNGEON).find((r) => r.items.some((i) => i.toLowerCase() === q))?.id;
}

/** The NPC who utters a given claim id (for critical-path reasoning). */
export function npcOfClaim(claimId: string): Npc | undefined {
  return Object.values(NPCS).find((n) => n.claims.some((c) => c.id === claimId));
}

/** Find an NPC present in a room by id or (partial) name. */
export function findNpcInRoom(roomId: string, query: string): Npc | undefined {
  const q = query.trim().toLowerCase();
  return npcsInRoom(roomId).find((n) => n.id === q || n.name.toLowerCase().includes(q) || q.includes(n.name.toLowerCase()));
}

/** Resolve which claim the player is confronting from a free-text description. */
export function findClaim(npc: Npc, about: string): Claim | undefined {
  const a = about.trim().toLowerCase();
  const byId = npc.claims.find((c) => c.id === a);
  if (byId) return byId;
  const words = a.split(/\W+/).filter((w) => w.length > 3);
  let best: Claim | undefined;
  let bestScore = 0;
  for (const c of npc.claims) {
    const hay = `${c.text} ${c.subject}`.toLowerCase();
    const score = words.filter((w) => hay.includes(w)).length;
    if (score > bestScore) {
      bestScore = score;
      best = c;
    }
  }
  return best ?? npc.claims.find((c) => c.lie);
}

/** A lie is exposable once the player has the refuting item or heard the contradiction. */
export function isExposable(claim: Claim, ev: Evidence): boolean {
  if (!claim.lie) return false;
  const need = claim.needs;
  if (!need) return true;
  if (need.item && ev.inventory.some((i) => i.toLowerCase() === need.item!.toLowerCase())) return true;
  if (need.heard && ev.heardClaims.includes(need.heard)) return true;
  return false;
}

/** All lie claims across NPCs, for ledger computation. */
export function allLies(): Array<{ npc: Npc; claim: Claim }> {
  return Object.values(NPCS).flatMap((npc) =>
    npc.claims.filter((c) => c.lie).map((claim) => ({ npc, claim })),
  );
}

/** NPCs the player has spoken with (heard at least one of their claims). */
export function metNpcs(heardClaims: string[]): Npc[] {
  const heard = new Set(heardClaims);
  return Object.values(NPCS).filter((n) => n.claims.some((c) => heard.has(c.id)));
}

/** Trust 0..100 — drops sharply once a person is caught lying. */
export function trustOf(npc: Npc, caughtClaims: string[]): number {
  const caught = new Set(caughtClaims);
  const lies = npc.claims.filter((c) => c.lie && caught.has(c.id)).length;
  return Math.max(5, Math.min(100, npc.baseTrust - lies * 40));
}

export type LedgerStatus = "caught" | "pending";
export interface LedgerEntry {
  npc: Npc;
  claim: Claim;
  status: LedgerStatus;
}

/** The contradiction ledger: lies that are caught, or pending (exposable, not yet confronted). */
export function ledger(ev: Evidence, caughtClaims: string[]): LedgerEntry[] {
  const caught = new Set(caughtClaims);
  const entries: LedgerEntry[] = [];
  for (const { npc, claim } of allLies()) {
    if (caught.has(claim.id)) entries.push({ npc, claim, status: "caught" });
    else if (isExposable(claim, ev)) entries.push({ npc, claim, status: "pending" });
  }
  return entries;
}
