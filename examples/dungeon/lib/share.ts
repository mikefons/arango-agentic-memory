/** Build the shareable "Guild Expedition" OG image URL from the current run (pure). */

export interface RunStats {
  room: string;
  items: number;
  lies: number;
  /** The hero who ran it (E-5) — persona name + glyph + expedition number. */
  hero?: string;
  glyph?: string;
  expedition?: number;
}

export function buildShareUrl(s: RunStats): string {
  const p = new URLSearchParams({
    room: s.room,
    items: String(s.items),
    lies: String(s.lies),
  });
  if (s.hero) p.set("hero", s.hero);
  if (s.glyph) p.set("glyph", s.glyph);
  if (s.expedition != null) p.set("expedition", String(s.expedition));
  return `/api/og?${p.toString()}`;
}
