import { describe, expect, it } from "vitest";
import {
  DUNGEON,
  START_ROOM,
  exitsOf,
  getRoom,
  itemInRoom,
  resolveMove,
} from "../lib/world";

describe("resolveMove", () => {
  it("returns the destination for a valid exit", () => {
    expect(resolveMove("gatehouse", "down")).toBe("cistern");
    expect(resolveMove("cistern", "north")).toBe("vault");
  });

  it("returns null for a missing exit or unknown room", () => {
    expect(resolveMove("gatehouse", "east")).toBeNull();
    expect(resolveMove("nowhere", "north")).toBeNull();
  });
});

describe("itemInRoom", () => {
  it("matches items case-insensitively and returns the canonical name", () => {
    expect(itemInRoom("vault", "BRASS KEY")).toBe("brass key");
    expect(itemInRoom("gatehouse", "rusted torch")).toBe("rusted torch");
  });

  it("returns null when the item is not present", () => {
    expect(itemInRoom("gatehouse", "brass key")).toBeNull();
  });
});

describe("exitsOf", () => {
  it("lists a room's exit directions", () => {
    expect(exitsOf("gatehouse").sort()).toEqual(["down", "north"]);
    expect(exitsOf("nowhere")).toEqual([]);
  });
});

describe("a short playthrough", () => {
  it("can walk Gatehouse → Cistern → Vault and find the brass key", () => {
    let room = getRoom(START_ROOM);
    expect(room.id).toBe("gatehouse");

    const toCistern = resolveMove(room.id, "down");
    expect(toCistern).toBe("cistern");
    room = getRoom(toCistern!);

    const toVault = resolveMove(room.id, "north");
    expect(toVault).toBe("vault");
    room = getRoom(toVault!);

    expect(itemInRoom(room.id, "brass key")).toBe("brass key");
  });
});

describe("world integrity", () => {
  it("every exit points to a room that exists", () => {
    for (const room of Object.values(DUNGEON)) {
      for (const dest of Object.values(room.exits)) {
        expect(DUNGEON[dest as string], `${room.id} → ${dest}`).toBeDefined();
      }
    }
  });

  it("the start room exists", () => {
    expect(DUNGEON[START_ROOM]).toBeDefined();
  });
});
