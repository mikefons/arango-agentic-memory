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
import {
  ACCUSE_THRESHOLD,
  DIRECTIONS,
  exitsOf,
  findClaim,
  findNpcInRoom,
  getRoom,
  isExposable,
  itemInRoom,
  resolveMove,
  type Dir,
} from "./world";
import { caughtCount, evidenceChain } from "./accuse";

export interface GameState {
  roomId: string;
  inventory: string[];
  heardClaims: string[];
  caughtClaims: string[];
  // Expedition lifecycle (E-1): the current hero (its own agent_id) + torch budget.
  expedition: number;
  heroId: string;
  torch: number;
  // Set once a hero dies to a false/unproven accusation (E-4) — NPCs grow warier.
  wary?: boolean;
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

    talk: tool({
      description: "Speak with a person in the current room and hear what they have to say.",
      inputSchema: z.object({ npc: z.string().describe("who to talk to (their name)") }),
      execute: async ({ npc }) => {
        const here = getRoom(state.roomId);
        const person = findNpcInRoom(here.id, npc);
        if (!person) {
          return { ok: false as const, reason: `There is no one called ${npc} in the ${here.name}.` };
        }
        // Persist each statement as testimony, and mint a claim entity per claim
        // so a later `confront` has a real key to supersede (best-effort, §15).
        for (const c of person.claims) {
          await remember(`${person.name} the ${person.role} claims: ${c.text}`, ctx);
          await core.seedEntity(c.subject, ctx).catch(() => undefined);
        }
        return {
          ok: true as const,
          npc: person.name,
          role: person.role,
          claims: person.claims.map((c) => ({ id: c.id, text: c.text })),
          heard: person.claims.map((c) => c.id),
        };
      },
    }),

    confront: tool({
      description:
        "Challenge a person about something they said. Succeeds only if you can back it up with evidence you've gathered.",
      inputSchema: z.object({
        npc: z.string().describe("who to confront"),
        about: z.string().describe("the statement or topic you're challenging"),
      }),
      execute: async ({ npc, about }) => {
        const here = getRoom(state.roomId);
        const person = findNpcInRoom(here.id, npc);
        if (!person) {
          return { ok: false as const, reason: `There is no one called ${npc} here.` };
        }
        const claim = findClaim(person, about);
        if (!claim) {
          return { ok: false as const, npc: person.name, reason: `${person.name} never said such a thing.` };
        }
        const exposable = isExposable(claim, {
          inventory: state.inventory,
          heardClaims: state.heardClaims,
        });
        if (!exposable) {
          const reason = claim.lie
            ? "You sense a lie, but you can't prove it yet — find what refutes it."
            : `${person.name} is telling the truth about that.`;
          return { ok: true as const, caught: false as const, npc: person.name, claimId: claim.id, reason };
        }
        // Caught: supersede the false fact in the memory core (bi-temporal §12).
        const lieKey = await core.seedEntity(claim.subject, ctx).catch(() => undefined);
        const truthKey = await core
          .seedEntity(claim.truth ?? `${claim.subject} (corrected)`, ctx)
          .catch(() => undefined);
        if (lieKey && truthKey) {
          await core.supersede(truthKey, lieKey, ctx).catch(() => undefined);
        }
        await remember(`${person.name}'s claim "${claim.subject}" was exposed as a lie.`, ctx);
        return {
          ok: true as const,
          caught: true as const,
          npc: person.name,
          claimId: claim.id,
          confession: claim.confession ?? `${person.name} falls silent, caught out.`,
        };
      },
    }),

    accuse: tool({
      description:
        "Formally accuse a person in this room of being the traitor. Succeeds only if the " +
        "guild has already exposed enough of their lies — proof that persists across " +
        "expeditions. A wrong or unproven accusation is fatal.",
      inputSchema: z.object({ npc: z.string().describe("who to accuse") }),
      execute: async ({ npc }) => {
        const here = getRoom(state.roomId);
        const person = findNpcInRoom(here.id, npc);
        if (!person) {
          return { ok: false as const, reason: `There is no one called ${npc} here to accuse.` };
        }
        // Resolve from the persistent guild graph — caught lies accumulate across heroes.
        const graph = await core.memoryGraph(ctx.tenant_id).catch(() => ({ nodes: [], edges: [] }));
        const caught = caughtCount(person, graph);
        if (person.traitor && caught >= ACCUSE_THRESHOLD) {
          return {
            ok: true as const,
            accuse: true as const,
            win: true as const,
            npc: person.name,
            caught,
            needed: ACCUSE_THRESHOLD,
            chain: evidenceChain(person, graph),
            confession:
              `${person.name}: "The guild remembers more than any one of you ever could. ` +
              `Yes. It was me."`,
          };
        }
        return {
          ok: true as const,
          accuse: true as const,
          win: false as const,
          npc: person.name,
          caught,
          needed: ACCUSE_THRESHOLD,
          reason: person.traitor
            ? "You lack the proof — the guild has caught too few of their lies. The accusation founders, and the keep turns on you."
            : `${person.name} is no traitor. The false accusation rings out, and the dark closes in.`,
        };
      },
    }),
  };
}
