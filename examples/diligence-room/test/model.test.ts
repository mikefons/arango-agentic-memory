import { describe, expect, it } from "vitest";
import { agentCallOptions } from "../lib/model";

describe("agentCallOptions", () => {
  it("turns extended thinking off and carries a fresh abort signal", () => {
    const a = agentCallOptions();
    expect(a.providerOptions.anthropic.thinking).toEqual({ type: "disabled" });
    expect(a.abortSignal).toBeInstanceOf(AbortSignal);
    expect(agentCallOptions().abortSignal).not.toBe(a.abortSignal);
  });
});
