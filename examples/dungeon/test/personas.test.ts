import { describe, expect, it } from "vitest";

import { persona, PERSONA_COUNT } from "../lib/personas";

describe("hero personas", () => {
  it("is deterministic per expedition", () => {
    expect(persona(3)).toEqual(persona(3));
  });

  it("gives each archetype a name, glyph, and voice", () => {
    for (let e = 1; e <= PERSONA_COUNT; e++) {
      const p = persona(e);
      expect(p.name).toBeTruthy();
      expect(p.glyph).toBeTruthy();
      expect(p.voice).toContain("This hero is");
    }
  });

  it("rotates archetypes and varies names across a full cycle", () => {
    const first = Array.from({ length: PERSONA_COUNT }, (_, i) => persona(i + 1));
    expect(new Set(first.map((p) => p.key)).size).toBe(PERSONA_COUNT); // all distinct archetypes
    expect(new Set(first.map((p) => p.name)).size).toBe(PERSONA_COUNT); // all distinct names
  });

  it("cycles archetype but changes name on the next lap", () => {
    const a = persona(1);
    const b = persona(1 + PERSONA_COUNT); // same archetype, next name
    expect(b.key).toBe(a.key);
    expect(b.name).not.toBe(a.name);
  });
});
