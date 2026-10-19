import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { Approval, CheckRun, Repair, Role } from "@/lib/types";

import { RepairActions } from "./repair-actions";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const repair = {
  id: "r1",
  status: "proposed",
  branch_name: "kmr-ai/fix-a",
  base_branch: "main",
  base_sha: "0123456789abcdef",
  diff_sha256: "a".repeat(64),
  files: [{ path: "index.html", action: "modify", additions: 1, deletions: 0 }],
} as Repair;

const run = (overall: CheckRun["overall"], statuses: CheckRun["results"]): CheckRun => ({
  id: "c1",
  repair_id: "r1",
  overall,
  results: statuses,
  diff_sha256: "a".repeat(64),
  base_sha: "0123456789abcdef",
  created_at: "2026-01-01T00:00:00Z",
});
const approval = (state: Approval["state"]): Approval => ({
  id: "a1",
  repair_id: "r1",
  decision: "approved",
  state,
  branch_name: "kmr-ai/fix-a",
  base_sha: "0123456789abcdef",
  diff_sha256: "a".repeat(64),
  acknowledged_incomplete_checks: false,
  note: "",
  expires_at: "2026-01-02T00:00:00Z",
  revoked_reason: null,
  created_at: "2026-01-01T00:00:00Z",
});

function setup(role: Role, checks: CheckRun[], approvals: Approval[] = []) {
  apiRequest.mockImplementation(async (method: string, path: string) => {
    if (method === "GET" && path.endsWith("/checks")) return { ok: true, data: checks };
    if (method === "GET" && path.endsWith("/approvals")) return { ok: true, data: approvals };
    return { ok: true, data: {} };
  });
  return render(
    <ToastProvider>
      <RepairActions orgId="o1" repair={repair} role={role} />
    </ToastProvider>,
  );
}
const ok = (name: string, status: CheckRun["results"][number]["status"]) => ({
  name,
  status,
  detail: "",
  duration_ms: 5,
});

beforeEach(() => {
  apiRequest.mockReset();
});

describe("RepairActions", () => {
  it("shows skipped and unavailable checks as exactly that, never as passed", async () => {
    setup("admin", [
      run("unavailable", [
        ok("syntax", "passed"),
        ok("javascript_syntax", "skipped"),
        ok("project_check", "unavailable"),
      ]),
    ]);
    expect(await screen.findByText("Skipped (did not run)")).toBeInTheDocument();
    expect(screen.getByText("Unavailable (could not run)")).toBeInTheDocument();
    expect(screen.getAllByText(/Nothing is counted as passed for them/).length).toBeGreaterThan(0);
  });

  it("cannot approve before checks, or after a failed check", async () => {
    const { unmount } = setup("admin", []);
    expect(await screen.findByRole("button", { name: /Approve this exact change/ })).toBeDisabled();
    unmount();
    setup("admin", [run("failed", [ok("syntax", "failed")])]);
    expect(await screen.findByRole("button", { name: /Approve this exact change/ })).toBeDisabled();
  });

  it("needs an explicit acknowledgement when not every check ran", async () => {
    setup("admin", [run("unavailable", [ok("project_check", "unavailable")])]);
    const approve = await screen.findByRole("button", { name: /Approve this exact change/ });
    expect(approve).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/not every check ran/));
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/repairs/r1/approvals", {
      decision: "approved",
      acknowledge_incomplete_checks: true,
    });
  });

  it("asks for confirmation before creating the branch, and says nothing is merged", async () => {
    setup("admin", [run("passed", [ok("syntax", "passed")])], [approval("active")]);
    await userEvent.click(await screen.findByRole("button", { name: "Create the branch" }));
    const dialog = screen.getByRole("dialog", { name: "Create this branch?" });
    expect(dialog).toHaveTextContent(/nothing is merged or deployed/i);
    expect(apiRequest).not.toHaveBeenCalledWith(
      "POST",
      expect.stringContaining("/apply"),
      expect.anything(),
    );
  });

  it("gives members the checks but not the decision", async () => {
    setup("member", [run("passed", [ok("syntax", "passed")])]);
    expect(await screen.findByRole("button", { name: /Run the checks again/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Approve this exact change/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Only an owner or admin can approve/)).toBeInTheDocument();
  });

  it("offers nothing to viewers", async () => {
    setup("viewer", []);
    await screen.findByText(/No checks have been run/);
    expect(screen.queryByRole("button", { name: /Run the checks/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("shows impact, effect and rollback before the approval buttons", async () => {
    setup("admin", [run("passed", [ok("syntax", "passed")])]);
    expect(await screen.findByText("Before you approve")).toBeInTheDocument();
    expect(screen.getByText(/Rollback: delete that branch/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing else is changed/)).toBeInTheDocument();
  });
});
