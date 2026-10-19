import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ScanCompare } from "@/lib/types";

import { CompareCard } from "./compare-card";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const other = {
  id: "s2",
  project_id: "p1",
  status: "completed",
  created_at: "2026-01-02T10:00:00Z",
  limits: { profile: { label: "Small phone" } },
};
const compare: ScanCompare = {
  basis: "Load times are measured by the scanner, not by real visitors.",
  first: { scan_id: "s1", profile: { key: "desktop", label: "Desktop" }, findings: 0 },
  second: { scan_id: "s2", profile: { key: "phone", label: "Small phone" }, findings: 1 },
  only_in_first: [],
  only_in_second: [{ kind: "a11y_issue", page: "/", severity: "warning" }],
  in_both: 2,
  load_ms: [{ page: "/", first: 300, second: null }],
};

describe("CompareCard", () => {
  beforeEach(() => apiRequest.mockReset());

  it("renders nothing when there is no other completed scan of the project", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: [{ ...other, project_id: "p9" }] });
    const { container } = render(
      <CompareCard orgId="o" scan={{ id: "s1", project_id: "p1", limits: {} }} />,
    );
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows what appears under only one profile and labels load times as measured", async () => {
    apiRequest.mockImplementation((...args: string[]) => {
      const path = args[1] ?? "";
      return Promise.resolve(
        path.includes("/compare/") ? { ok: true, data: compare } : { ok: true, data: [other] },
      );
    });
    render(<CompareCard orgId="o" scan={{ id: "s1", project_id: "p1", limits: {} }} />);
    await userEvent.selectOptions(await screen.findByLabelText("Compare with"), "s2");
    expect(await screen.findByText(/a11y issue on \/ \(warning\)/)).toBeInTheDocument();
    expect(screen.getByText(/not by real visitors/)).toBeInTheDocument();
    expect(screen.getByText("not measured")).toBeInTheDocument();
  });
});
