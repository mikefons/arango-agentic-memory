/** Pure helpers for the (gated) AI scene-art feature. */

/** A stable Blob key fragment for a room name. */
export function roomSlug(room: string): string {
  return (
    room
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "") || "room"
  );
}

/** The image prompt for a room — dark-fantasy, on-theme, text-free. */
export function scenePrompt(room: string): string {
  return (
    "Dark fantasy dungeon concept art, atmospheric and candle-lit, moody, " +
    `painterly, no text or words: ${room}, deep within a ruined keep called Ashfall Keep.`
  );
}
