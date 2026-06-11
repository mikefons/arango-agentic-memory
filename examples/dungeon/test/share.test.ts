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
});
