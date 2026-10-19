import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { appRoutes, mobileTabHrefs } from "./routes";

describe("appRoutes", () => {
  it("has unique hrefs", () => {
    const hrefs = appRoutes.map((route) => route.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it("has a page file for every route", () => {
    for (const { href } of appRoutes) {
      const page = join(process.cwd(), "src/app/(app)", href, "page.tsx");
      expect(existsSync(page), `missing page for ${href}`).toBe(true);
    }
  });

  it("lists every page of the product", () => {
    expect(appRoutes.map((r) => r.href)).toEqual([
      "/dashboard",
      "/projects",
      "/scans",
      "/issues",
      "/repairs",
      "/approvals",
      "/history",
      "/assistant",
      "/settings",
    ]);
  });

  it("only puts known pages in the mobile tab bar", () => {
    for (const href of mobileTabHrefs) {
      expect(
        appRoutes.some((r) => r.href === href),
        href,
      ).toBe(true);
    }
  });
});
