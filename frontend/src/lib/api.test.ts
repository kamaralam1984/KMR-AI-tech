import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchReadiness } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(impl: () => Promise<unknown>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("fetchReadiness", () => {
  it("returns the readiness report", async () => {
    const body = { status: "ready", checks: { database: "reachable" } };
    stubFetch(async () => ({ json: async () => body }));
    expect(await fetchReadiness()).toEqual({ kind: "ok", readiness: body });
  });

  it("treats a 503 not_ready body as a valid answer, not an outage", async () => {
    const body = { status: "not_ready", checks: { redis: "unreachable" } };
    stubFetch(async () => ({ status: 503, json: async () => body }));
    expect(await fetchReadiness()).toEqual({ kind: "ok", readiness: body });
  });

  it("reports unreachable when the request fails", async () => {
    stubFetch(async () => {
      throw new Error("connect ECONNREFUSED");
    });
    expect(await fetchReadiness()).toEqual({ kind: "unreachable" });
  });

  it("reports unreachable when the body has an unexpected shape", async () => {
    stubFetch(async () => ({ json: async () => ({ hello: "world" }) }));
    expect(await fetchReadiness()).toEqual({ kind: "unreachable" });
  });
});
