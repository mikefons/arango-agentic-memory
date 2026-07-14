import { describe, expect, it } from "vitest";

import {
  absorb,
  caseProgress,
  EMPTY_GUILD,
  guildStats,
  loseHero,
  roomMatches,
  roomTint,
  ROOMS_TOTAL,
} from "../lib/guild";

describe("absorb", () => {
  it("unions new rooms and claims into the guild's knowledge", () => {
    const next = absorb(EMPTY_GUILD, ["gatehouse", "barracks"], ["veld-key"]);
    expect(next.roomsSeen).toEqual(["gatehouse", "barracks"]);
    expect(next.claimsHeard).toEqual(["veld-key"]);
  });

  it("de-duplicates and returns the same object when nothing is new (skip persist)", () => {
    const base = { heroesLost: 0, roomsSeen: ["gatehouse"], claimsHeard: ["veld-key"] };
    expect(absorb(base, ["gatehouse"], ["veld-key"])).toBe(base);
    const grown = absorb(base, ["gatehouse", "cistern"], ["veld-key"]);
    expect(grown).not.toBe(base);
    expect(grown.roomsSeen).toEqual(["gatehouse", "cistern"]);
  });
});

describe("loseHero", () => {
  it("increments only the heroes-lost tally", () => {
    const next = loseHero({ heroesLost: 2, roomsSeen: ["a"], claimsHeard: ["b"] });
    expect(next.heroesLost).toBe(3);
    expect(next.roomsSeen).toEqual(["a"]);
  });
});

describe("caseProgress", () => {
  it("computes pct and solved, clamped to the threshold", () => {
    expect(caseProgress(0, 4)).toEqual({ caught: 0, needed: 4, pct: 0, solved: false });
    expect(caseProgress(2, 4)).toEqual({ caught: 2, needed: 4, pct: 50, solved: false });
    expect(caseProgress(4, 4)).toEqual({ caught: 4, needed: 4, pct: 100, solved: true });
    expect(caseProgress(9, 4).pct).toBe(100); // never over 100
    expect(caseProgress(-3, 4).caught).toBe(0); // never negative
  });
});

describe("guildStats", () => {
  it("derives map-fill % from rooms seen over the total", () => {
    const save = { heroesLost: 1, roomsSeen: ["gatehouse", "barracks"], claimsHeard: ["x"] };
    const stats = guildStats(save, { expeditions: 3, liesCaught: 5, caught: 2, needed: 4 });
    expect(stats.roomsTotal).toBe(ROOMS_TOTAL);
    expect(stats.mapFillPct).toBe(Math.round((2 / ROOMS_TOTAL) * 100));
    expect(stats.expeditions).toBe(3);
    expect(stats.liesCaught).toBe(5);
    expect(stats.case.solved).toBe(false);
  });
});

describe("roomMatches / roomTint", () => {
  it("matches fuzzily on either-way containment", () => {
    expect(roomMatches("The Gatehouse", "gatehouse")).toBe(true);
    expect(roomMatches("Archive", "The Archive, dusty")).toBe(true);
    expect(roomMatches("Vault", "Cistern")).toBe(false);
  });

  it("tints the current room, visited rooms, and guild-only memory distinctly", () => {
    const visited = ["The Gatehouse", "The Barracks"];
    expect(roomTint("The Gatehouse", "The Gatehouse", visited)).toBe("here");
    expect(roomTint("The Barracks", "The Gatehouse", visited)).toBe("visited");
    expect(roomTint("The Drowned Vault", "The Gatehouse", visited)).toBe("guild");
  });
});
