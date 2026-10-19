import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { BackendEvidence as Evidence, Role } from "@/lib/types";

import { BackendEvidence } from "./backend-evidence";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const sources = (logs: "available" | "not_connected" = "not_connected"): Evidence["sources"] => [
  {
    key: "browser",
    label: "Browser (observed)",
    state: "available",
    detail: "What the browser saw.",
  },
  {
    key: "api_checks",
    label: "API checks (observed by KMR AI)",
    state: "not_connected",
    detail: "No API checks are set up for this project.",
  },
  {
    key: "customer_logs",
    label: "Backend logs (supplied by you)",
    state: logs,
    detail:
      logs === "available"
        ? "3 log entries uploaded."
        : "No backend logs were uploaded for this scan.",
  },
  {
    key: "monitoring",
    label: "Monitoring (Sentry, Datadog, OpenTelemetry)",
    state: "not_connected",
    detail: "Monitoring connectors are not available yet.",
  },
  {
    key: "repository",
    label: "Source code repository",
    state: "not_connected",
    detail: "No repository is connected, so nothing is derived from your code.",
  },
];

const noData: Evidence = {
  backend_telemetry_available: false,
  notice:
    "Backend telemetry is unavailable for this scan: no API checks ran and no backend logs were uploaded.",
  sources: sources(),
  correlations: [
    {
      observation_id: "o1",
      kind: "http_error",
      page_url: "https://shop.example.com/",
      request_url: "https://shop.example.com/api/products",
      http_status: 500,
      request_id: "kmr-abc-1",
      classification: "no_backend_data",
      needs_review: true,
      statement: "The browser saw a failure. No backend telemetry covers this request.",
      sources: ["browser_observed"],
      evidence: [
        {
          source: "browser_observed",
          ref_type: "observation",
          ref_id: "o1",
          summary: "The browser received HTTP 500.",
        },
      ],
    },
  ],
};

const confirmed: Evidence = {
  backend_telemetry_available: true,
  notice: null,
  sources: sources("available"),
  correlations: [
    {
      ...noData.correlations[0],
      classification: "backend_confirmed",
      needs_review: false,
      statement: "The browser and the backend both show a problem for this request.",
      sources: ["browser_observed", "customer_supplied"],
      evidence: [
        noData.correlations[0].evidence[0],
        {
          source: "customer_supplied",
          ref_type: "log_entry",
          ref_id: "l1",
          summary: "Backend log: HTTP 500, error",
        },
      ],
    },
  ],
};

function renderCard(role: Role = "member") {
  return render(
    <ToastProvider>
      <BackendEvidence orgId="o1" scanId="s1" role={role} status="completed" />
    </ToastProvider>,
  );
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe("BackendEvidence", () => {
  it("says plainly when there is no backend telemetry, and lists every source with its state", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: noData });
    renderCard();
    expect(await screen.findByText(/Backend telemetry is unavailable/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Evidence sources" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(within(list).getAllByText("Not connected").length).toBe(4);
    expect(within(list).getByText("Available")).toBeInTheDocument();
    expect(screen.getByText(/Monitoring connectors are not available yet/)).toBeInTheDocument();
  });

  it("marks a failure with no backend data as needs review, never as a cause", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: noData });
    renderCard();
    expect(await screen.findByText("Needs review: no backend data")).toBeInTheDocument();
    expect(screen.getByText(/No backend telemetry covers this request/)).toBeInTheDocument();
    expect(screen.queryByText(/root cause/i)).not.toBeInTheDocument();
  });

  it("labels where each piece of evidence came from", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: confirmed });
    renderCard();
    expect(await screen.findByText("Seen on both sides")).toBeInTheDocument();
    expect(screen.getByText("Observed in the browser:")).toBeInTheDocument();
    expect(screen.getByText("Supplied by you:")).toBeInTheDocument();
    expect(screen.getByText("kmr-abc-1")).toBeInTheDocument();
    expect(screen.queryByText(/Backend telemetry is unavailable/)).not.toBeInTheDocument();
  });

  it("uploads logs, then reloads the evidence", async () => {
    apiRequest.mockImplementation(async (method: string, path: string) => {
      if (method === "POST")
        return { ok: true, data: { accepted: 2, rejected: 1, total_for_scan: 2 } };
      return { ok: true, data: path.endsWith("backend-evidence") ? noData : null };
    });
    renderCard();
    await screen.findByText(/Backend telemetry is unavailable/);
    await userEvent.click(screen.getByRole("button", { name: /Upload backend logs/ }));
    const dialog = screen.getByRole("dialog", { name: "Upload backend logs" });
    await userEvent.type(within(dialog).getByLabelText("Log lines"), '{{"request_id":"kmr-abc-1"}');
    await userEvent.click(within(dialog).getByRole("button", { name: "Add logs" }));
    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/scans/s1/backend-logs", {
        format: "jsonl",
        content: '{"request_id":"kmr-abc-1"}',
      }),
    );
    expect(await screen.findByText("2 log lines added")).toBeInTheDocument();
    const gets = apiRequest.mock.calls.filter(([m]) => m === "GET").length;
    expect(gets).toBeGreaterThanOrEqual(2); // loaded again after the upload
  });

  it("refuses an empty upload without calling the API", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: noData });
    renderCard();
    await screen.findByText(/Backend telemetry is unavailable/);
    await userEvent.click(screen.getByRole("button", { name: /Upload backend logs/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add logs" }));
    expect(screen.getByText("Paste some log lines first.")).toBeInTheDocument();
    expect(apiRequest.mock.calls.filter(([m]) => m === "POST")).toHaveLength(0);
  });

  it("offers to remove uploaded logs only when there are some", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: confirmed });
    const { unmount } = renderCard();
    expect(await screen.findByRole("button", { name: "Remove uploaded logs" })).toBeInTheDocument();
    unmount();
    apiRequest.mockResolvedValue({ ok: true, data: noData });
    renderCard();
    await screen.findByText(/Backend telemetry is unavailable/);
    expect(screen.queryByRole("button", { name: "Remove uploaded logs" })).not.toBeInTheDocument();
  });

  it("gives viewers the evidence but no upload", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: confirmed });
    renderCard("viewer");
    await screen.findByText("Seen on both sides");
    expect(screen.queryByRole("button", { name: /Upload backend logs/ })).not.toBeInTheDocument();
  });

  it("does not pretend when the evidence cannot be loaded", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: { status: 500, message: "x", fieldErrors: {} },
    });
    renderCard();
    expect(await screen.findByText("Could not load backend evidence")).toBeInTheDocument();
  });
});
