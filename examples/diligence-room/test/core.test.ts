import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeCoreUrl, resetRoom, roomTenant } from "../lib/core";

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

describe("resetRoom", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("soft-deletes only the Room's own tenant via /v1/forget with write scope", async () => {
    const fetchMock = vi.fn((_url: string | URL | Request, _init?: RequestInit) =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "forgotten", counts: { memories: 11 } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await resetRoom("Northwind Robotics");

    expect(res).toEqual({ status: "forgotten", counts: { memories: 11 } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/v1\/forget$/);
    expect(init?.method).toBe("POST");
    const body = JSON.parse(init?.body as string);
    expect(body.tenant_id).toBe("room:northwind-robotics"); // never a bare tenant / another demo
    expect(body.access_level).toBe("write");
  });
});
