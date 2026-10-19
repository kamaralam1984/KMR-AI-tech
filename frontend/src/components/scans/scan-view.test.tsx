import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { ObservationPage, Role, ScanDetail } from "@/lib/types";

import { ScanView } from "./scan-view";

// The evidence card has its own tests; here it is replaced so these tests count only the view.
vi.mock("./backend-evidence", () => ({ BackendEvidence: () => null }));
vi.mock("./diagnose-panel", () => ({ DiagnosePanel: () => null }));
vi.mock("./compare-card", () => ({ CompareCard: () => null }));

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const scan = (over: Partial<ScanDetail> = {}): ScanDetail => ({
  id: "s1",
  project_id: "p1",
  project_name: "Shop",
  status: "completed",
  site_url: "https://shop.example.com/",
  created_at: "2026-01-01T10:00:00Z",
  started_at: "2026-01-01T10:00:01Z",
  finished_at: "2026-01-01T10:00:09Z",
  error_code: null,
  stopped_reason: null,
  cancel_requested: false,
  summary: { pages_scanned: 3, requests: 40, bytes: 2048, duration_ms: 8000 },
  limits: {},
  counts: { page: 3, console_error: 1 },
  severity_counts: { info: 3, error: 1 },
  ...over,
});

const observations: ObservationPage = {
  total: 2,
  items: [
    {
      id: "o1",
      kind: "page",
      severity: "info",
      page_url: "https://shop.example.com/",
      data: { title: "Home", status: 200, load_ms: 90 },
      evidence_id: "e1",
      created_at: "2026-01-01T10:00:02Z",
    },
    {
      id: "o2",
      kind: "console_error",
      severity: "error",
      page_url: "https://shop.example.com/about",
      data: { message: "boom" },
      evidence_id: null,
      created_at: "2026-01-01T10:00:03Z",
    },
  ],
};

function renderView(role: Role = "member", s: ScanDetail = scan(), obs = observations) {
  return render(
    <ToastProvider>
      <ScanView orgId="o1" role={role} initialScan={s} initialObservations={obs} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  apiRequest.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("ScanView", () => {
  it("shows the summary and plain-language observations", () => {
    renderView();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText(/“Home”, HTTP 200, loaded in 90 ms/)).toBeInTheDocument();
    expect(screen.getByText("Console error: boom")).toBeInTheDocument();
    expect(screen.getByText(/does not\s+yet explain causes/i)).toBeInTheDocument();
    for (const badge of ["Info", "Error"])
      expect(screen.getAllByText(badge).length).toBeGreaterThan(0);
  });

  it("explains a failure in words, not codes", () => {
    renderView("member", scan({ status: "failed", error_code: "site_unreachable" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByText("site_unreachable")).not.toBeInTheDocument();
  });

  it("tells you when a limit cut the scan short", () => {
    renderView("member", scan({ stopped_reason: "page_limit" }));
    expect(screen.getByText(/Stopped at the page limit/)).toBeInTheDocument();
  });

  it("offers cancel only to people who may cancel, and only while active", () => {
    const running = scan({ status: "running", summary: {} });
    const { unmount } = renderView("member", running);
    expect(screen.getByRole("button", { name: /Cancel scan/ })).toBeInTheDocument();
    unmount();
    renderView("viewer", running);
    expect(screen.queryByRole("button", { name: /Cancel scan/ })).not.toBeInTheDocument();
  });

  it("does not offer cancel for a finished scan", () => {
    renderView();
    expect(screen.queryByRole("button", { name: /Cancel scan/ })).not.toBeInTheDocument();
  });

  it("asks for confirmation, then cancels", async () => {
    apiRequest.mockResolvedValue({
      ok: true,
      data: scan({ status: "running", cancel_requested: true }),
    });
    renderView("member", scan({ status: "running", summary: {} }));
    await userEvent.click(screen.getByRole("button", { name: /^Cancel scan$/ }));
    const dialog = screen.getByRole("dialog", { name: "Cancel this scan?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel scan" }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/scans/s1/cancel");
    expect(await screen.findByText("Cancelling…")).toBeInTheDocument();
  });

  it("filters by kind through the API", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { total: 1, items: [observations.items[1]] } });
    renderView();
    await userEvent.click(screen.getByRole("button", { name: /Console errors \(1\)/ }));
    expect(apiRequest).toHaveBeenCalledWith(
      "GET",
      "/organizations/o1/scans/s1/observations?limit=100&offset=0&kind=console_error",
    );
    expect(screen.getByRole("button", { name: /Console errors/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("opens a screenshot through the tenant-checked API route", async () => {
    renderView();
    await userEvent.click(
      screen.getByRole("button", { name: /View screenshot of https:\/\/shop.example.com\/$/ }),
    );
    const image = await screen.findByRole("img", {
      name: /Screenshot of https:\/\/shop.example.com\//,
    });
    expect(image).toHaveAttribute("src", "/api/organizations/o1/scans/s1/evidence/e1");
  });

  it("refreshes while the scan is active and stops by itself when it finishes", async () => {
    vi.useFakeTimers();
    apiRequest.mockImplementation(async (...args: unknown[]) => {
      const path = String(args[1]);
      return path.includes("/observations")
        ? { ok: true, data: observations }
        : { ok: true, data: scan({ status: "completed" }) };
    });
    renderView("member", scan({ status: "running", summary: {} }));
    expect(screen.getByText("Running")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(screen.getByText("Completed")).toBeInTheDocument();
    const callsWhenDone = apiRequest.mock.calls.length;
    expect(callsWhenDone).toBeGreaterThanOrEqual(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(apiRequest.mock.calls.length).toBe(callsWhenDone); // polling stopped
  });
});
