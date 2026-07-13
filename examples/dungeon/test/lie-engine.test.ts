import { describe, expect, it } from "vitest";
import {
  findClaim,
  findNpcInRoom,
  isExposable,
  ledger,
  metNpcs,
  trustOf,
  NPCS,
} from "../lib/world";

const veld = NPCS.veld;

describe("findNpcInRoom", () => {
  it("finds an NPC present in a room by name", () => {
    expect(findNpcInRoom("barracks", "Veld")?.id).toBe("veld");
    expect(findNpcInRoom("archive", "mara")?.id).toBe("mara");
  });
  it("returns undefined when no such NPC is in the room", () => {
    expect(findNpcInRoom("gatehouse", "Veld")).toBeUndefined();
    expect(findNpcInRoom("barracks", "Saro")).toBeUndefined();
  });
});

describe("findClaim", () => {
  it("resolves a claim from free text", () => {
    expect(findClaim(veld, "the only key")?.id).toBe("veld-key");
    expect(findClaim(veld, "storm drain")?.id).toBe("veld-drain");
  });
  it("falls back to the lie when the text is vague", () => {
    expect(findClaim(veld, "you're hiding something")?.id).toBe("veld-key");
  });
});

describe("isExposable", () => {
  const empty = { inventory: [], heardClaims: [] };
  it("a lie is not exposable without evidence", () => {
    expect(isExposable(veld.claims[0], empty)).toBe(false);
  });
  it("the brass key OR Mara's ledger exposes Veld's lie", () => {
    expect(isExposable(veld.claims[0], { inventory: ["brass key"], heardClaims: [] })).toBe(true);
    expect(isExposable(veld.claims[0], { inventory: [], heardClaims: ["mara-ledger"] })).toBe(true);
  });
  it("a truthful claim is never exposable", () => {
    expect(isExposable(veld.claims[1], { inventory: ["brass key"], heardClaims: [] })).toBe(false);
  });
});

describe("trustOf", () => {
  it("drops sharply once caught lying", () => {
    expect(trustOf(veld, [])).toBe(45);
    expect(trustOf(veld, ["veld-key"])).toBe(5);
  });
});

describe("ledger", () => {
  it("lists an exposable lie as pending, then caught once confronted", () => {
    const ev = { inventory: ["brass key"], heardClaims: [] };
    const veldPending = ledger(ev, []).find((e) => e.claim.id === "veld-key");
    expect(veldPending?.status).toBe("pending");

    const veldCaught = ledger(ev, ["veld-key"]).find((e) => e.claim.id === "veld-key");
    expect(veldCaught?.status).toBe("caught");
  });
  it("hides lies with no gathered evidence", () => {
    expect(ledger({ inventory: [], heardClaims: [] }, [])).toEqual([]);
  });
});

describe("metNpcs", () => {
  it("counts an NPC as met once any claim is heard", () => {
    expect(metNpcs(["veld-key"]).map((n) => n.id)).toEqual(["veld"]);
    expect(metNpcs([])).toEqual([]);
  });
});
