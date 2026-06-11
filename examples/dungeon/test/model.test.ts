import { describe, expect, it } from "vitest";
import { chooseProvider } from "../lib/model";

describe("chooseProvider", () => {
  it("prefers the Gateway when its key is present", () => {
    expect(chooseProvider({ AI_GATEWAY_API_KEY: "g", ANTHROPIC_API_KEY: "a" })).toBe("gateway");
    expect(chooseProvider({ AI_GATEWAY_API_KEY: "g" })).toBe("gateway");
  });

  it("falls back to Anthropic when only the Anthropic key is set", () => {
    expect(chooseProvider({ ANTHROPIC_API_KEY: "a" })).toBe("anthropic");
  });

  it("defaults to Gateway when neither key is set (error surfaces in the UI)", () => {
    expect(chooseProvider({})).toBe("gateway");
  });
});
