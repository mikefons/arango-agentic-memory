/**
 * Accusation logic (GUILD.md E-4) — resolves the traitor endgame from the *persistent*
 * guild graph, not per-hero state, so caught lies accumulate across expeditions.
 *
 * A lie is "caught" when its subject entity has been superseded in the memory core
 * (`confront` → `core.supersede`), which surfaces in `/v1/graph` as a node carrying
 * `invalid_at`. Pure + unit-testable.
 */

import { isSuperseded, type MemoryGraph } from "./explorer";
import { npcOfClaim, roomOfItem, type Npc } from "./world";

/** Names of every superseded (caught) entity in the guild graph. */
function superseded(graph: MemoryGraph): Set<string> {
  return new Set(graph.nodes.filter(isSuperseded).map((n) => n.name));
}

/** How many of an NPC's lies the guild has exposed (persisted across expeditions). */
export function caughtCount(npc: Npc, graph: MemoryGraph): number {
  const caught = superseded(graph);
  return npc.claims.filter((c) => c.lie && caught.has(c.subject)).length;
}

/** truth ⇒ lie pairs for the caught lies — the evidence chain shown on a win. */
export function evidenceChain(npc: Npc, graph: MemoryGraph): Array<{ lie: string; truth: string }> {
  const caught = superseded(graph);
  return npc.claims
    .filter((c) => c.lie && c.truth && caught.has(c.subject))
    .map((c) => ({ lie: c.subject, truth: c.truth as string }));
}

/**
 * A structural lower bound on the actions needed to expose a traitor: one confront per
 * lie, plus each distinct item to collect, each distinct NPC to hear, and each distinct
 * room to stand in. If this exceeds a hero's torch, the case cannot be closed in one
 * expedition — which is the whole point (it must span the guild ledger).
 */
export function criticalPathLength(npc: Npc): number {
  const lies = npc.claims.filter((c) => c.lie);
  const items = new Set<string>();
  const talks = new Set<string>([npc.id]); // must hear the traitor's own claims
  const rooms = new Set<string>([npc.room]);
  for (const c of lies) {
    if (c.needs?.item) {
      items.add(c.needs.item.toLowerCase());
      const r = roomOfItem(c.needs.item);
      if (r) rooms.add(r);
    }
    if (c.needs?.heard) {
      const src = npcOfClaim(c.needs.heard);
      if (src) {
        talks.add(src.id);
        rooms.add(src.room);
      }
    }
  }
  return lies.length + items.size + talks.size + rooms.size;
}
