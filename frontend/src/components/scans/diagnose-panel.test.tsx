import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { Role, ScanStatus } from "@/lib/types";

import { DiagnosePanel } from "./diagnose-panel";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const issueRow = {
  id: "i1",
  title: "Images return HTTP 404",
  severity: "medium",
  classification: "diagnosed",
};

function setup(role: Role = "member", status: ScanStatus = "completed") {
  apiRequest.mockImplementation(async (method: string, path: string) => {
    if (path === "/ai/status")
      return {
        ok: true,
        data: {
          provider: "rules",
          model: "rules-2",
          is_ai: false,
          label: "Built-in rules (not AI)",
        },
      };
    if (method === "GET") return { ok: true, data: [] };
    return {
      ok: true,
      data: { run: { issues_count: 1, dropped_issues: 2, flags: [] }, issues: [issueRow] },
    };
  });
  return render(
    <ToastProvider>
      <DiagnosePanel orgId="o1" scanId="s1" role={role} status={status} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe("DiagnosePanel", () => {
  it("runs a diagnosis, lists the issues and reports discarded output", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Diagnose this scan/ }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/scans/s1/diagnose");
    expect(await screen.findByRole("link", { name: "Images return HTTP 404" })).toHaveAttribute(
      "href",
      "/issues/i1",
    );
    expect(screen.getByText(/2 proposed issue\(s\) were discarded/)).toBeInTheDocument();
  });

  it("says the rules are not AI", async () => {
    setup();
    expect(await screen.findByText(/built-in rules, not by AI/i)).toBeInTheDocument();
  });

  it("does not offer diagnosis to viewers or before the scan completes", async () => {
    const { unmount } = setup("viewer");
    await screen.findByText(/No issues recorded/);
    expect(screen.queryByRole("button", { name: /Diagnose/ })).not.toBeInTheDocument();
    unmount();
    setup("member", "running");
    expect(screen.queryByRole("button", { name: /Diagnose/ })).not.toBeInTheDocument();
    expect(screen.getByText(/once the scan has completed/)).toBeInTheDocument();
  });

  it("shows the API's message when diagnosis fails", async () => {
    setup();
    apiRequest.mockImplementation(async (method: string) =>
      method === "POST"
        ? { ok: false, error: { message: "Daily diagnosis limit reached." } }
        : { ok: true, data: [] },
    );
    await userEvent.click(await screen.findByRole("button", { name: /Diagnose this scan/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Daily diagnosis limit reached.");
  });

  it("asks whether to plan a fix instead of assuming", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Diagnose this scan/ }));
    expect(await screen.findByText(/Would you like KMR AI to plan a fix/)).toBeInTheDocument();
  });
});
