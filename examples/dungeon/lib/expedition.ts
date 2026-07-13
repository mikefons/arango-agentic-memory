/**
 * Expedition lifecycle (GUILD.md E-1) — heroes are expendable agents; the guild
 * ledger outlives them.
 *
 * Each expedition is a fresh hero (its own `agent_id`) with a **torch** (a turn
 * budget = the context window as a game resource). When the torch dies the hero
 * retires; only what was written to the shared `guild::query` tier survives, and the
 * next hero reads it via `read_agent_ids` (MA-2). Pure + unit-testable.
 */

export const TORCH_BUDGET = 12;

/** The shared crew tier all heroes read/write — the guild's persistent memory (§14). */
export const GUILD_TIER = "guild::query";

export interface Expedition {
  expedition: number;
  heroId: string;
  torch: number;
}

export function heroId(expedition: number): string {
  return `hero-${expedition}`;
}

export function firstExpedition(): Expedition {
  return { expedition: 1, heroId: heroId(1), torch: TORCH_BUDGET };
}

/** The next hero: fresh id + a full torch. */
export function nextExpedition(current: number): Expedition {
  const n = current + 1;
  return { expedition: n, heroId: heroId(n), torch: TORCH_BUDGET };
}

export function spendTorch(torch: number): number {
  return Math.max(0, torch - 1);
}

export function torchSpent(torch: number): boolean {
  return torch <= 0;
}
