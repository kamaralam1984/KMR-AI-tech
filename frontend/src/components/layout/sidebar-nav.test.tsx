import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { appRoutes } from "@/lib/routes";

import { SidebarNav } from "./sidebar-nav";

vi.mock("next/navigation", () => ({ usePathname: () => "/scans" }));

describe("SidebarNav", () => {
  it("links to every route", () => {
    render(<SidebarNav />);
    for (const route of appRoutes) {
      expect(screen.getByRole("link", { name: new RegExp(route.label) })).toHaveAttribute(
        "href",
        route.href,
      );
    }
  });

  it("marks only the current page", () => {
    render(<SidebarNav />);
    const current = screen.getAllByRole("link").filter((l) => l.hasAttribute("aria-current"));
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAttribute("href", "/scans");
  });
});
