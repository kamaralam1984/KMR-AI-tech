import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "./client-api";

afterEach(() => vi.unstubAllGlobals());

type Reply = { status?: number; body?: unknown };

function stubFetch(replies: Record<string, Reply>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const reply = replies[url] ?? { status: 404 };
      const status = reply.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => reply.body,
      };
    }),
  );
  return calls;
}

describe("apiRequest", () => {
  it("sends no CSRF header on GET", async () => {
    const calls = stubFetch({ "/api/auth/me": { body: { ok: 1 } } });
    await apiRequest("GET", "/auth/me");
    expect(calls).toHaveLength(1);
    expect((calls[0].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("fetches a CSRF token first and sends it on state-changing calls", async () => {
    const calls = stubFetch({
      "/api/auth/csrf": { body: { csrf_token: "tok123" } },
      "/api/organizations": { status: 201, body: { id: "o1" } },
    });
    const result = await apiRequest("POST", "/organizations", { name: "Acme" });
    expect(result).toEqual({ ok: true, data: { id: "o1" } });
    expect(calls[0].url).toBe("/api/auth/csrf");
    expect((calls[1].init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
    expect(calls[1].init?.credentials).toBe("same-origin");
  });

  it("skips CSRF for login and register", async () => {
    const calls = stubFetch({ "/api/auth/login": { body: { id: "u1" } } });
    await apiRequest("POST", "/auth/login", { email: "a@b.c", password: "x" }, { csrf: false });
    expect(calls).toHaveLength(1);
  });

  it("does not send a state-changing request when no CSRF token can be obtained", async () => {
    const calls = stubFetch({ "/api/auth/csrf": { status: 401 } });
    const result = await apiRequest("DELETE", "/organizations/o1/members/u1");
    expect(result).toMatchObject({ ok: false, error: { status: 401 } });
    expect(calls).toHaveLength(1);
  });

  it("maps validation errors to fields without losing the message", async () => {
    stubFetch({
      "/api/auth/register": {
        status: 422,
        body: {
          error: {
            message: "Request validation failed.",
            details: [{ location: ["body", "password"], message: "too short" }],
          },
        },
      },
    });
    const result = await apiRequest("POST", "/auth/register", {}, { csrf: false });
    expect(result).toMatchObject({
      ok: false,
      error: { status: 422, fieldErrors: { password: "too short" } },
    });
  });

  it("reports a network failure as status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    const result = await apiRequest("GET", "/auth/me");
    expect(result).toMatchObject({ ok: false, error: { status: 0 } });
  });

  it("returns undefined data for 204", async () => {
    stubFetch({
      "/api/auth/csrf": { body: { csrf_token: "t" } },
      "/api/auth/logout": { status: 204 },
    });
    expect(await apiRequest("POST", "/auth/logout")).toEqual({ ok: true, data: undefined });
  });
});
