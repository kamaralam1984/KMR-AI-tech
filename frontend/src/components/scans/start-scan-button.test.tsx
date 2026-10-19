import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StartScanButton } from "./start-scan-button";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

beforeEach(() => {
  push.mockReset();
  apiRequest.mockReset();
});

describe("StartScanButton", () => {
  it("is disabled and says why when scanning is not allowed yet", () => {
    render(
      <StartScanButton
        orgId="o"
        projectId="p"
        blockedReason="Verify that you own the website first."
        activeScanId={null}
      />,
    );
    expect(screen.getByRole("button", { name: /Start scan/ })).toBeDisabled();
    expect(screen.getByText("Verify that you own the website first.")).toBeInTheDocument();
  });

  it("points to the running scan instead of offering a second one", () => {
    render(<StartScanButton orgId="o" projectId="p" blockedReason={null} activeScanId="s9" />);
    expect(screen.queryByRole("button", { name: /Start scan/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View it" })).toHaveAttribute("href", "/scans/s9");
  });

  it("offers a browser choice only when the server lists more than one", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { id: "s2" } });
    const { rerender } = render(
      <StartScanButton orgId="o" projectId="p" blockedReason={null} activeScanId={null} />,
    );
    expect(screen.queryByLabelText("Browser")).not.toBeInTheDocument();
    rerender(
      <StartScanButton
        orgId="o"
        projectId="p"
        blockedReason={null}
        activeScanId={null}
        engines={["chromium", "firefox"]}
      />,
    );
    await userEvent.selectOptions(screen.getByLabelText("Browser"), "firefox");
    await userEvent.click(screen.getByRole("button", { name: /Start scan/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/scans/s2"));
    expect(apiRequest.mock.calls[0][1]).toBe("/organizations/o/projects/p/scans?engine=firefox");
  });

  it("starts a scan with an idempotency key and opens it", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { id: "s1" } });
    render(<StartScanButton orgId="o" projectId="p" blockedReason={null} activeScanId={null} />);
    await userEvent.click(screen.getByRole("button", { name: /Start scan/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/scans/s1"));
    const [method, path, body, options] = apiRequest.mock.calls[0];
    expect([method, path, body]).toEqual(["POST", "/organizations/o/projects/p/scans", undefined]);
    expect(options.headers["Idempotency-Key"]).toMatch(/[0-9a-f-]{8,}/);
  });

  it("shows why the server refused", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: {
        status: 409,
        message: "A scan is already queued or running for this project.",
        fieldErrors: {},
      },
    });
    render(<StartScanButton orgId="o" projectId="p" blockedReason={null} activeScanId={null} />);
    await userEvent.click(screen.getByRole("button", { name: /Start scan/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already queued or running");
    expect(push).not.toHaveBeenCalled();
  });

  it("states that a scan never changes anything", () => {
    render(<StartScanButton orgId="o" projectId="p" blockedReason={null} activeScanId={null} />);
    expect(screen.getByText(/never submits forms/i)).toBeInTheDocument();
  });
});
