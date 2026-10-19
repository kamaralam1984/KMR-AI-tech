import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { ApiCheck } from "@/lib/types";

import { ApiChecksCard } from "./api-checks-card";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const check = (over: Partial<ApiCheck> = {}): ApiCheck => ({
  id: "c1",
  name: "Products",
  path: "/api/products",
  method: "GET",
  expected_status: 200,
  max_latency_ms: 3000,
  response_schema: null,
  has_credentials: false,
  auth_header_name: null,
  last_run: null,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

const renderCard = (checks: ApiCheck[], canWrite = true, verified = true) =>
  render(
    <ToastProvider>
      <ApiChecksCard
        orgId="o1"
        projectId="p1"
        checks={checks}
        canWrite={canWrite}
        verified={verified}
      />
    </ToastProvider>,
  );

beforeEach(() => {
  apiRequest.mockReset();
  refresh.mockReset();
});

describe("ApiChecksCard", () => {
  it("shows the last result in words, including why it failed", () => {
    renderCard([
      check({
        last_run: {
          id: "r1",
          check_id: "c1",
          scan_id: null,
          triggered_by: "manual",
          status: "failed",
          http_status: 500,
          latency_ms: 40,
          response_bytes: 2,
          created_at: "2026-01-01T00:00:00Z",
          reasons: [{ code: "status_mismatch", expected: 200, actual: 500 }],
        },
      }),
    ]);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("expected HTTP 200, got 500");
  });

  it("adds a check with the values from the form, including a schema and a write-only credential", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: check() });
    renderCard([]);
    await userEvent.click(screen.getByRole("button", { name: "Add an API check" }));
    const dialog = screen.getByRole("dialog", { name: "Add an API check" });
    await userEvent.type(within(dialog).getByLabelText(/^Name/), "Orders");
    await userEvent.clear(within(dialog).getByLabelText(/Path on your website/));
    await userEvent.type(within(dialog).getByLabelText(/Path on your website/), "/api/orders");
    await userEvent.type(
      within(dialog).getByLabelText(/Expected response shape/),
      '{{"type":"object"}',
    );
    await userEvent.type(within(dialog).getByLabelText("Header name"), "Authorization");
    await userEvent.type(within(dialog).getByLabelText("Header value"), "Bearer abc");
    expect(within(dialog).getByLabelText("Header value")).toHaveAttribute("type", "password");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add check" }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/projects/p1/api-checks", {
      name: "Orders",
      path: "/api/orders",
      expected_status: 200,
      max_latency_ms: 3000,
      response_schema: { type: "object" },
      credentials: { header_name: "Authorization", value: "Bearer abc" },
    });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("rejects a schema that is not JSON before calling the API", async () => {
    renderCard([]);
    await userEvent.click(screen.getByRole("button", { name: "Add an API check" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Name/), "X");
    await userEvent.type(within(dialog).getByLabelText(/Expected response shape/), "not json");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add check" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent(/not valid JSON/);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("shows the server's reason when saving fails", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: {
        status: 422,
        message: "The path must start with a single / and be at most 1024 characters.",
        fieldErrors: {},
      },
    });
    renderCard([]);
    await userEvent.click(screen.getByRole("button", { name: "Add an API check" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/^Name/), "X");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add check" }));
    expect(await within(dialog).findByText(/path must start with a single/)).toBeInTheDocument();
  });

  it("never shows a stored credential, only that one exists", async () => {
    renderCard([check({ has_credentials: true, auth_header_name: "X-Api-Key" })]);
    expect(screen.getByText(/uses X-Api-Key/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit Products" }));
    const dialog = screen.getByRole("dialog", { name: "Edit API check" });
    expect(within(dialog).getByText(/A credential is stored for header/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Header value")).toHaveValue("");
  });

  it("runs a check on demand and shows the verdict", async () => {
    apiRequest.mockResolvedValue({
      ok: true,
      data: {
        id: "r1",
        check_id: "c1",
        scan_id: null,
        triggered_by: "manual",
        status: "error",
        http_status: null,
        latency_ms: 5,
        response_bytes: null,
        created_at: "2026-01-01T00:00:00Z",
        reasons: [{ code: "timeout" }],
      },
    });
    renderCard([check()]);
    await userEvent.click(screen.getByRole("button", { name: "Run Products now" }));
    expect(apiRequest).toHaveBeenCalledWith(
      "POST",
      "/organizations/o1/projects/p1/api-checks/c1/run",
    );
    expect(await screen.findByText("Could not run")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("the request timed out");
  });

  it("cannot run checks before the website is verified, and viewers cannot change anything", () => {
    const { unmount } = renderCard([check()], true, false);
    expect(screen.getByRole("button", { name: "Run Products now" })).toBeDisabled();
    expect(screen.getByText("Verify your website to run checks.")).toBeInTheDocument();
    unmount();
    renderCard([check()], false, true);
    for (const name of [
      /Run Products now/,
      /Edit Products/,
      /Remove Products/,
      /Add an API check/,
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("asks before removing a check", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: undefined });
    renderCard([check()]);
    await userEvent.click(screen.getByRole("button", { name: "Remove Products" }));
    const dialog = screen.getByRole("dialog", { name: "Remove this API check?" });
    expect(apiRequest).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(apiRequest).toHaveBeenCalledWith(
      "DELETE",
      "/organizations/o1/projects/p1/api-checks/c1",
    );
  });
});
