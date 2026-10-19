import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TrafficSummary } from "@/lib/types";

import { TrafficCard } from "./traffic-card";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const empty: TrafficSummary = {
  present: false,
  source_label: null,
  period_start: null,
  period_end: null,
  total_views: 0,
  pages: 0,
  rows_rejected: 0,
  top_pages: [],
  notice: "These numbers come from your own upload. KMR AI did not measure or verify them.",
};

describe("TrafficCard", () => {
  beforeEach(() => apiRequest.mockReset());

  it("says whose numbers these are and that nothing is uploaded yet", () => {
    render(<TrafficCard orgId="o" projectId="p" traffic={empty} canEdit />);
    expect(screen.getByText(/did not measure or verify/)).toBeInTheDocument();
    expect(screen.getByText(/No traffic uploaded/)).toBeInTheDocument();
  });

  it("uploads the pasted CSV with the period and source", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { ...empty, present: true, rows_rejected: 2 } });
    render(<TrafficCard orgId="o" projectId="p" traffic={empty} canEdit />);
    await userEvent.type(screen.getByLabelText(/Where the numbers come from/), "My analytics");
    await userEvent.type(screen.getByLabelText(/Period start/), "2026-10-01");
    await userEvent.type(screen.getByLabelText(/Period end/), "2026-10-07");
    await userEvent.click(screen.getByLabelText("Or paste the CSV"));
    await userEvent.paste("path,views\n/,10");
    await userEvent.click(screen.getByRole("button", { name: "Upload traffic" }));
    expect(apiRequest).toHaveBeenCalledWith("PUT", "/organizations/o/projects/p/traffic", {
      source_label: "My analytics",
      period_start: "2026-10-01",
      period_end: "2026-10-07",
      csv: "path,views\n/,10",
    });
    expect(await screen.findByText(/2 row\(s\) could not be read/)).toBeInTheDocument();
  });

  it("offers no upload to people who cannot edit", () => {
    render(<TrafficCard orgId="o" projectId="p" traffic={empty} canEdit={false} />);
    expect(screen.queryByRole("button", { name: "Upload traffic" })).toBeNull();
  });
});
