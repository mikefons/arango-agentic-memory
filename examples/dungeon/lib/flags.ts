/**
 * Config-gated feature toggles (Showcase polish).
 *
 * All features are OFF by default, so the dungeon behaves exactly as before
 * unless you opt in. Turn them on with env vars locally, or override at runtime
 * via Vercel **Edge Config** (a `dungeon` key holding a partial Flags object) —
 * read only when an `EDGE_CONFIG` connection string is present.
 */

export interface Flags {
  /** Generate + show AI room scene art (needs an image provider + Blob token). */
  sceneArt: boolean;
  /** DM weaves a gentle, diegetic hint toward an unsolved contradiction. */
  hint: boolean;
}

export const DEFAULT_FLAGS: Flags = { sceneArt: false, hint: false };

const truthy = (v: string | undefined) => v === "1" || v === "true";

/** Pure env → flags (unit-tested); Edge Config can override these at runtime. */
export function flagsFromEnv(env: Record<string, string | undefined> = process.env): Flags {
  return {
    sceneArt: truthy(env.SCENE_ART),
    hint: truthy(env.DUNGEON_HINT),
  };
}

export async function getFlags(): Promise<Flags> {
  let flags = flagsFromEnv();
  if (process.env.EDGE_CONFIG) {
    try {
      const { get } = await import("@vercel/edge-config");
      const override = (await get("dungeon")) as Partial<Flags> | undefined;
      if (override && typeof override === "object") flags = { ...flags, ...override };
    } catch {
      /* Edge Config unreachable → fall back to env/defaults */
    }
  }
  return flags;
}
