import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { Repair, Repository, Role } from "@/lib/types";

import { RepairPanel } from "./repair-panel";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const repo: Repository = {
  id: "r1",
  project_id: "p1",
  provider: "local_git",
  location: "/srv/site",
  default_branch: "main",
  has_token: false,
  check_command: null,
  head_sha: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const plan: Repair = {
  id: "abcdef12-0000",
  issue_id: "i1",
  project_id: "p1",
  status: "proposed",
  source: "rules",
  provider: "rules",
  model: "repair-rules-1",
  is_ai: false,
  base_branch: "main",
  base_sha: "0123456789abcdef",
  branch_name: "kmr-ai/fix-x",
  explanation: "Done.",
  files: [{ path: "index.html", action: "modify", additions: 1, deletions: 0 }],
  diff: "+x\n",
  diff_sha256: "a".repeat(64),
  findings: [],
  warnings: [],
  created_at: "2026-01-01T00:00:00Z",
};

function setup(
  role: Role = "member",
  opts: { repository?: Repository | null; open?: boolean } = {},
) {
  const repository = opts.repository === undefined ? repo : opts.repository;
  apiRequest.mockImplementation(async (method: string, path: string) => {
    if (path.endsWith("/repository")) return { ok: true, data: repository };
    if (method === "POST") return { ok: true, data: plan };
    return { ok: true, data: [] };
  });
  return render(
    <ToastProvider>
      <RepairPanel
        orgId="o1"
        projectId="p1"
        issueId="i1"
        role={role}
        issueOpen={opts.open ?? true}
      />
    </ToastProvider>,
  );
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe("RepairPanel", () => {
  it("plans a fix and shows the plan", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Plan a fix/ }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/issues/i1/repairs", {});
    expect(await screen.findByLabelText(/Repair plan abcdef12/)).toBeInTheDocument();
  });

  it("asks for a repository first", async () => {
    setup("member", { repository: null });
    expect(await screen.findByText("No repository connected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Plan a fix/ })).not.toBeInTheDocument();
  });

  it("offers nothing to viewers, and nothing for a closed issue", async () => {
    const { unmount } = setup("viewer");
    await screen.findByText(/No repair plans/);
    expect(screen.queryByRole("button", { name: /Plan a fix/ })).not.toBeInTheDocument();
    unmount();
    setup("member", { open: false });
    expect(await screen.findByText(/Only an open issue/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Plan a fix/ })).not.toBeInTheDocument();
  });

  it("shows the API's message when planning fails", async () => {
    setup();
    apiRequest.mockImplementation(async (method: string) =>
      method === "POST"
        ? {
            ok: false,
            error: { message: "Your organization reached its daily repair planning limit." },
          }
        : { ok: true, data: [] },
    );
    await userEvent.click(await screen.findByRole("button", { name: /Plan a fix/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/daily repair planning limit/);
  });
});
