/** Build the shareable "Dungeon Run" OG image URL from the current run (pure). */

export interface RunStats {
  room: string;
  items: number;
  lies: number;
}

export function buildShareUrl(s: RunStats): string {
  const p = new URLSearchParams({
    room: s.room,
    items: String(s.items),
    lies: String(s.lies),
  });
  return `/api/og?${p.toString()}`;
}
