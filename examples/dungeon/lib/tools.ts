/**
 * Dungeon tools (3.5c-1) — the agent's actions over the world.
 *
 * Each `execute` validates against the static world (lib/world.ts) and persists
 * the resulting fact to the memory core (best-effort: a core hiccup degrades to a
 * memory-less turn, never breaks the game — §15). Tool outputs are structured so
 * the client can fold them back into game state; the `arangoMemory()` middleware
 * separately captures these calls as procedural memory.
 */

import { tool } from "ai";
import { z } from "zod";
import * as core from "./core";
import type { Ctx } from "./types";
import { DIRECTIONS, exitsOf, getRoom, itemInRoom, resolveMove, type Dir } from "./world";

export interface GameState {
  roomId: string;
  inventory: string[];
}

const remember = (content: string, ctx: Ctx) =>
  core.store(content, ctx, { mode: "full" }).catch(() => undefined);

export function makeTools(ctx: Ctx, state: GameState) {
  return {
    look: tool({
      description:
        "Describe the room the player is currently in — its sights, the exits, and any items present.",
      inputSchema: z.object({}),
      execute: async () => {
        const room = getRoom(state.roomId);
        const exits = exitsOf(room.id);
        const items = room.items.filter((i) => !state.inventory.includes(i));
        await remember(
          `In the ${room.name}: ${room.description} Exits lead ${exits.join(", ")}.` +
            (items.length ? ` Items here: ${items.join(", ")}.` : ""),
          ctx,
        );
        return { roomId: room.id, name: room.name, description: room.description, exits, items };
      },
    }),

    move: tool({
      description: "Move the player through an exit. Direction is one of north/south/east/west/up/down.",
      inputSchema: z.object({ direction: z.enum(DIRECTIONS as [Dir, ...Dir[]]) }),
      execute: async ({ direction }) => {
        const here = getRoom(state.roomId);
        const destId = resolveMove(here.id, direction);
        if (!destId) {
          return { ok: false as const, roomId: here.id, reason: `There is no exit ${direction} from the ${here.name}.` };
        }
        const dest = getRoom(destId);
        await remember(`The ${here.name} connects ${direction} to the ${dest.name}.`, ctx);
        return {
          ok: true as const,
          roomId: dest.id,
          name: dest.name,
          description: dest.description,
          exits: exitsOf(dest.id),
        };
      },
    }),

    take: tool({
      description: "Pick up an item that is present in the current room.",
      inputSchema: z.object({ item: z.string().describe("the item to pick up") }),
      execute: async ({ item }) => {
        const here = getRoom(state.roomId);
        const found = itemInRoom(here.id, item);
        if (!found) {
          return { ok: false as const, item, reason: `There is no ${item} here to take.` };
        }
        if (state.inventory.includes(found)) {
          return { ok: false as const, item: found, reason: `You already carry the ${found}.` };
        }
        await remember(`The player picked up the ${found} in the ${here.name}.`, ctx);
        return { ok: true as const, item: found, roomId: here.id };
      },
    }),
  };
}
