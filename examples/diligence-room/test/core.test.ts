import { describe, expect, it } from "vitest";
import { normalizeCoreUrl, roomTenant } from "../lib/core";

describe("normalizeCoreUrl", () => {
  it("strips trailing slashes so paths never double up", () => {
    expect(normalizeCoreUrl("http://127.0.0.1:8080/")).toBe("http://127.0.0.1:8080");
    expect(normalizeCoreUrl("http://127.0.0.1:8080///")).toBe("http://127.0.0.1:8080");
    expect(normalizeCoreUrl("https://core.example.com")).toBe("https://core.example.com");
  });
});

describe("roomTenant", () => {
  it("namespaces every Room under room:<slug> — never the dungeon's tenant", () => {
    expect(roomTenant("Northwind Robotics")).toBe("room:northwind-robotics");
    expect(roomTenant("acme_2026")).toBe("room:acme_2026");
    expect(roomTenant("  Weird / Name!! ")).toBe("room:weird-name");
  });

  it("is stable and collision-free against the dungeon tenant", () => {
    expect(roomTenant("dungeon-player")).toBe("room:dungeon-player");
    expect(roomTenant("dungeon-player")).not.toBe("dungeon-player");
  });

  it("falls back to a safe slug for empty input", () => {
    expect(roomTenant("   ")).toBe("room:untitled");
  });
});
