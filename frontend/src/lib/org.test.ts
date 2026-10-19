import { describe, expect, it } from "vitest";

import { assignableRoles, atLeast, canRemove, pickActiveOrg, safeNext } from "./org";
import type { OrgSummary } from "./types";

const orgs: OrgSummary[] = [
  { id: "a", name: "A", role: "owner" },
  { id: "b", name: "B", role: "viewer" },
];

describe("pickActiveOrg", () => {
  it("uses the preferred organization when the user still belongs to it", () => {
    expect(pickActiveOrg(orgs, "b")?.id).toBe("b");
  });
  it("falls back to the first organization for an unknown or missing preference", () => {
    expect(pickActiveOrg(orgs, "gone")?.id).toBe("a");
    expect(pickActiveOrg(orgs, null)?.id).toBe("a");
  });
  it("returns null when the user has no organization", () => {
    expect(pickActiveOrg([], "a")).toBeNull();
  });
});

describe("assignableRoles (mirrors the API rules)", () => {
  it("lets owners assign any role", () => {
    expect(assignableRoles("owner", "viewer")).toEqual(["owner", "admin", "member", "viewer"]);
    expect(assignableRoles("owner", null)).toHaveLength(4);
  });
  it("lets admins manage only members and viewers, and only up to member", () => {
    expect(assignableRoles("admin", null)).toEqual(["member", "viewer"]);
    expect(assignableRoles("admin", "viewer")).toEqual(["member", "viewer"]);
    expect(assignableRoles("admin", "admin")).toEqual([]);
    expect(assignableRoles("admin", "owner")).toEqual([]);
  });
  it("gives members and viewers no management rights", () => {
    expect(assignableRoles("member", "viewer")).toEqual([]);
    expect(assignableRoles("viewer", null)).toEqual([]);
  });
});

describe("canRemove", () => {
  it("lets anyone leave but only managers remove others", () => {
    expect(canRemove("viewer", "viewer", true)).toBe(true);
    expect(canRemove("member", "viewer", false)).toBe(false);
    expect(canRemove("admin", "member", false)).toBe(true);
    expect(canRemove("admin", "owner", false)).toBe(false);
    expect(canRemove("owner", "admin", false)).toBe(true);
  });
});

describe("atLeast", () => {
  it("orders roles", () => {
    expect(atLeast("admin", "member")).toBe(true);
    expect(atLeast("viewer", "member")).toBe(false);
    expect(atLeast("owner", "owner")).toBe(true);
  });
});

describe("safeNext", () => {
  it("keeps same-site paths", () => {
    expect(safeNext("/projects?x=1")).toBe("/projects?x=1");
  });
  it.each([
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "",
    null,
    undefined,
  ])("rejects %s", (value) => {
    expect(safeNext(value as string | null | undefined)).toBe("/dashboard");
  });
});
