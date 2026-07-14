import { describe, expect, it } from "vitest";
import { buildShareUrl } from "../lib/share";

describe("buildShareUrl", () => {
  it("encodes the run stats into the OG image URL", () => {
    const url = buildShareUrl({ room: "The Drowned Vault", items: 3, lies: 1 });
    expect(url.startsWith("/api/og?")).toBe(true);
    const q = new URLSearchParams(url.split("?")[1]);
    expect(q.get("room")).toBe("The Drowned Vault");
    expect(q.get("items")).toBe("3");
    expect(q.get("lies")).toBe("1");
  });

  it("omits the hero fields when absent (backward compatible)", () => {
    const q = new URLSearchParams(buildShareUrl({ room: "X", items: 0, lies: 0 }).split("?")[1]);
    expect(q.has("hero")).toBe(false);
    expect(q.has("expedition")).toBe(false);
  });

  it("encodes the hero persona + expedition when present (E-5)", () => {
    const url = buildShareUrl({
      room: "The Counting House", items: 2, lies: 4,
      hero: "Brann the Bold", glyph: "⚔️", expedition: 7,
    });
    const q = new URLSearchParams(url.split("?")[1]);
    expect(q.get("hero")).toBe("Brann the Bold");
    expect(q.get("glyph")).toBe("⚔️");
    expect(q.get("expedition")).toBe("7");
  });
});
