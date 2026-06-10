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
    exits: { up: "smithy", south: "vault" },
    items: ["sealed dispatch"],
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
