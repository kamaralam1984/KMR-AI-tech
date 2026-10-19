import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BottomNav } from "./bottom-nav";

vi.mock("next/navigation", () => ({ usePathname: () => "/projects" }));

describe("BottomNav", () => {
  it("shows the live tabs and marks the current one", () => {
    render(<BottomNav onMore={() => {}} />);
    expect(screen.getByRole("link", { name: /Scans/ })).toHaveAttribute("href", "/scans");
    expect(screen.getByRole("link", { name: /Dashboard/ })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: /Projects/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Settings/ })).not.toHaveAttribute("aria-current");
  });

  it("does not put planned (not yet available) pages in the tab bar", () => {
    render(<BottomNav onMore={() => {}} />);
    for (const label of ["Issues", "Repairs", "Approvals"]) {
      expect(screen.queryByRole("link", { name: new RegExp(label) })).not.toBeInTheDocument();
    }
  });

  it("opens the full navigation from More", async () => {
    const onMore = vi.fn();
    render(<BottomNav onMore={onMore} />);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(onMore).toHaveBeenCalled();
  });
});
