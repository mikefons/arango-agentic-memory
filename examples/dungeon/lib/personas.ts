/**
 * Hero personas (GUILD.md E-3) — a display + voice layer over the expedition number.
 *
 * The memory identity stays `hero-N` (see lib/expedition.ts); a persona only gives a
 * hero a *name, glyph, and voice* so consecutive expeditions read as different
 * characters inheriting one guild ledger. Deterministic per expedition. Pure.
 */

export interface Persona {
  key: string;
  glyph: string;
  name: string;
  /** Appended to DM_SYSTEM to color this hero's narration. */
  voice: string;
}

interface Archetype {
  key: string;
  glyph: string;
  temperament: string;
  names: string[];
}

const ARCHETYPES: Archetype[] = [
  {
    key: "knight",
    glyph: "⚔️",
    temperament: "an arrogant knight — brave to a fault, quick to boast, contemptuous of caution",
    names: ["Brann the Bold", "Sir Aldric", "Dame Yorrick"],
  },
  {
    key: "bard",
    glyph: "🎻",
    temperament: "a cowardly bard — flowery of speech, prone to nervous asides and dramatic dread",
    names: ["Piped Lyle", "Wren the Warbler", "Cassian Quill"],
  },
  {
    key: "golem",
    glyph: "🗿",
    temperament: "a literal-minded golem — blunt and precise, baffled by metaphor and idle talk",
    names: ["Unit Nine", "Clay", "Basalt"],
  },
  {
    key: "gravedigger",
    glyph: "🕯️",
    temperament: "a superstitious gravedigger — mutters wards, reads omens into everything, unhurried",
    names: ["Old Mabon", "Silla Ash", "Fenwick"],
  },
  {
    key: "alchemist",
    glyph: "⚗️",
    temperament: "an over-caffeinated alchemist — rapid and tangential, forever theorising aloud",
    names: ["Fizz", "Doctor Quell", "Mirabel Fume"],
  },
  {
    key: "assassin",
    glyph: "🗡️",
    temperament: "a retired assassin — terse and watchful, economical with words and with trust",
    names: ["The Grey", "Vesh", "Old Kite"],
  },
];

const VOICE_FRAME =
  "Narrate this hero in the voice below — let their temperament color the second-person " +
  "narration and their occasional asides, but keep the frame and never use lists. " +
  "This hero is ";

/** The persona for an expedition (1-indexed). Deterministic; archetypes round-robin, names rotate. */
export function persona(expedition: number): Persona {
  const n = ARCHETYPES.length;
  const idx = (((expedition - 1) % n) + n) % n;
  const a = ARCHETYPES[idx];
  const cycle = Math.floor(Math.max(0, expedition - 1) / n);
  const name = a.names[cycle % a.names.length];
  return { key: a.key, glyph: a.glyph, name, voice: `\n\n${VOICE_FRAME}${a.temperament}.` };
}

export const PERSONA_COUNT = ARCHETYPES.length;
