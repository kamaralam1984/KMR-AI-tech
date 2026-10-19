import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { Repository } from "@/lib/types";

import { RepositoryCard } from "./repository-card";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const repo: Repository = {
  id: "r1",
  project_id: "p1",
  provider: "github",
  location: "acme/site",
  default_branch: "main",
  has_token: true,
  check_command: null,
  head_sha: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderCard(repository: Repository | null, canEdit = true) {
  return render(
    <ToastProvider>
      <RepositoryCard orgId="o1" projectId="p1" repository={repository} canEdit={canEdit} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe("RepositoryCard", () => {
  it("never shows a stored token, only that one exists", () => {
    renderCard(repo);
    expect(screen.getByText("Stored encrypted")).toBeInTheDocument();
    expect(screen.getByText(/never changed by KMR AI/)).toBeInTheDocument();
  });

  it("connects a repository and sends the token only when typed", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: repo });
    renderCard(null);
    await userEvent.selectOptions(screen.getByLabelText("Repository type"), "github");
    await userEvent.type(screen.getByLabelText(/Repository \(owner\/name\)/), "acme/site");
    await userEvent.type(screen.getByLabelText("Access token"), "ghp_secret");
    await userEvent.click(screen.getByRole("button", { name: "Connect repository" }));
    expect(apiRequest).toHaveBeenCalledWith(
      "PUT",
      "/organizations/o1/projects/p1/repository",
      expect.objectContaining({ provider: "github", location: "acme/site", token: "ghp_secret" }),
    );
  });

  it("shows the server's reason when connecting fails", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: { message: "That path is outside the allowed folders." },
    });
    renderCard(null);
    await userEvent.type(screen.getByLabelText(/Folder path/), "/etc");
    await userEvent.click(screen.getByRole("button", { name: "Connect repository" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/outside the allowed folders/);
  });

  it("is read-only for people who cannot administer", () => {
    renderCard(repo, false);
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
  });
});
