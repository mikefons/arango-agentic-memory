/**
 * Pure visual mappings for the evidence graph (DR-3b) — kept out of the React components so
 * the "what maps to what" is unit-testable. Node size = salience, accent hue = community
 * (related parties share a hue), belief drives a meter + border strength.
 */

/** Analyst-palette community hues. Index wraps, so any community count is safe. */
export const COMMUNITY_PALETTE = [
  "#4f8ef7", // blue
  "#a879f0", // violet
  "#f2a13d", // amber
  "#37c2a8", // teal
  "#ec6a9c", // pink
  "#6ee29b", // green
  "#e5675f", // red
  "#8ea2b8", // slate
] as const;

export function communityHue(community: number): string {
  const n = COMMUNITY_PALETTE.length;
  return COMMUNITY_PALETTE[(((community % n) + n) % n)];
}

const MIN_W = 132;
const MAX_W = 240;
export const NODE_HEIGHT = 48;

/** Node width in px, scaled by salience (PageRank) — central entities read as bigger. */
export function nodeWidth(salience: number): number {
  return Math.round(MIN_W + clamp01(salience) * (MAX_W - MIN_W));
}

/** Belief as a percentage (for the confidence meter under each node). */
export function beliefPct(belief: number): number {
  return Math.round(clamp01(belief) * 100);
}

/** Border opacity 0.35..1 — higher-belief entities look more solid. */
export function beliefBorderAlpha(belief: number): number {
  return 0.35 + clamp01(belief) * 0.65;
}

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}
