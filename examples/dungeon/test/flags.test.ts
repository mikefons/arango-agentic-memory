import { describe, expect, it } from "vitest";
import { DEFAULT_FLAGS, flagsFromEnv } from "../lib/flags";

describe("flagsFromEnv", () => {
  it("is all-off by default (no env)", () => {
    expect(flagsFromEnv({})).toEqual(DEFAULT_FLAGS);
  });

  it("enables flags from 1 / true", () => {
    expect(flagsFromEnv({ DUNGEON_HINT: "1", SCENE_ART: "true" })).toEqual({
      hint: true,
      sceneArt: true,
    });
  });

  it("ignores other truthy-looking values", () => {
    expect(flagsFromEnv({ DUNGEON_HINT: "yes", SCENE_ART: "0" })).toEqual(DEFAULT_FLAGS);
  });
});
