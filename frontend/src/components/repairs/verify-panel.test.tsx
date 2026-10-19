import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { Role } from "@/lib/types";

import { VerifyPanel } from "./verify-panel";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const scan = { id: "s2", status: "completed", created_at: "2026-02-01T00:00:00Z" };
const result = (outcome: string) => ({
  id: "v1",
  issue_id: "i1",
  repair_id: null,
  before_scan_id: "s1",
  after_scan_id: "s2",
  outcome,
  reasons: ["Reason text."],
  details: { pages_not_retested: ["https://x.example.com/"] },
  verified_diff_sha256: null,
  via_kmr_ai: false,
  created_at: "2026-02-01T01:00:00Z",
});

function setup(role: Role = "member", scans: unknown[] = [scan], runs: unknown[] = []) {
  apiRequest.mockImplementation(async (method: string, path: string) => {
    if (method === "POST") return { ok: true, data: result("needs_review") };
    if (path.includes("/verifications")) return { ok: true, data: runs };
    return { ok: true, data: scans };
  });
  return render(
    <ToastProvider>
      <VerifyPanel
        orgId="o1"
        projectId="p1"
        issueId="i1"
        issueCreatedAt="2026-01-01T00:00:00Z"
        role={role}
      />
    </ToastProvider>,
  );
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe("VerifyPanel", () => {
  it("verifies against a newer scan and shows the honest outcome with its reason", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Check whether it is fixed/ }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/issues/i1/verify", {
      after_scan_id: "s2",
    });
    expect(await screen.findByText("Needs Review")).toBeInTheDocument();
    expect(screen.getByText("Reason text.")).toBeInTheDocument();
    expect(screen.getByText(/Not re-tested: https:\/\/x\.example\.com\//)).toBeInTheDocument();
  });

  it("explains how to get a newer scan when none exists", async () => {
    setup("member", []);
    expect(await screen.findByText(/No finished scan newer than this issue/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Check whether/ })).not.toBeInTheDocument();
  });

  it("shows earlier results and offers nothing to viewers", async () => {
    setup("viewer", [scan], [{ ...result("fixed"), via_kmr_ai: true }]);
    expect(await screen.findByText("Fixed")).toBeInTheDocument();
    expect(screen.getByText(/fix made through KMR AI/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Check whether/ })).not.toBeInTheDocument();
  });

  it("shows the server's reason when verification is refused", async () => {
    setup();
    apiRequest.mockImplementation(async (method: string) =>
      method === "POST"
        ? { ok: false, error: { message: "That scan started before the fix branch was created." } }
        : { ok: true, data: method === "GET" ? [scan] : [] },
    );
    await userEvent.click(await screen.findByRole("button", { name: /Check whether it is fixed/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/before the fix branch/);
  });
});
