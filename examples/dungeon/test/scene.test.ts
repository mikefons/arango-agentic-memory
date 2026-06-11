import { describe, expect, it } from "vitest";
import { roomSlug, scenePrompt } from "../lib/scene";

describe("roomSlug", () => {
  it("slugifies a room name for a stable blob key", () => {
    expect(roomSlug("The Drowned Vault")).toBe("the-drowned-vault");
    expect(roomSlug("Gatehouse")).toBe("gatehouse");
  });
  it("never produces an empty slug", () => {
    expect(roomSlug("!!!")).toBe("room");
  });
});

describe("scenePrompt", () => {
  it("includes the room and stays on-theme + text-free", () => {
    const p = scenePrompt("The Cistern");
    expect(p).toContain("The Cistern");
    expect(p.toLowerCase()).toContain("no text");
  });
});
