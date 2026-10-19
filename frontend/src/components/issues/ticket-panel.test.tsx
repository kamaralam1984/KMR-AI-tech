import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TicketPanel } from "./ticket-panel";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

describe("TicketPanel", () => {
  beforeEach(() => apiRequest.mockReset());

  it("fetches nothing until asked, then shows the exact text before any sharing", async () => {
    apiRequest.mockResolvedValue({
      ok: true,
      data: {
        format: "markdown",
        title: "Broken checkout",
        filename: "broken-checkout.md",
        content: "# Broken checkout\n\n## Observed",
        content_sha256: "a".repeat(64),
        github: {
          available: false,
          reason: "Connect a GitHub repository to this project first.",
          repository: null,
          existing_url: null,
        },
        notice: "Review this text before you share it.",
      },
    });
    render(<TicketPanel orgId="o" issueId="i" />);
    expect(apiRequest).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Prepare ticket/ }));
    expect(apiRequest).toHaveBeenCalledWith("GET", "/organizations/o/issues/i/ticket");
    expect(await screen.findByDisplayValue(/# Broken checkout/)).toHaveAttribute("readonly");
    expect(screen.getByText("Review this text before you share it.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Email draft/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/^mailto:\?subject=Broken%20checkout/),
    );
    expect(screen.getByText(/Connect a GitHub repository/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Create GitHub issue/ })).toBeNull();
  });

  it("sends to GitHub only after the checkbox, and with the hash of the text it showed", async () => {
    const ticket = {
      format: "markdown",
      title: "T",
      filename: "t.md",
      content: "# T",
      content_sha256: "b".repeat(64),
      github: { available: true, reason: null, repository: "acme/site", existing_url: null },
      notice: "n",
    };
    apiRequest.mockResolvedValueOnce({ ok: true, data: ticket });
    apiRequest.mockResolvedValueOnce({
      ok: true,
      data: { url: "https://github.com/acme/site/issues/1" },
    });
    render(<TicketPanel orgId="o" issueId="i" />);
    await userEvent.click(screen.getByRole("button", { name: /Prepare ticket/ }));
    const send = await screen.findByRole("button", { name: /Create GitHub issue/ });
    expect(send).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(send);
    expect(apiRequest).toHaveBeenLastCalledWith("POST", "/organizations/o/issues/i/github-issue", {
      content_sha256: "b".repeat(64),
      confirm: true,
    });
    expect(await screen.findByRole("link", { name: /issues\/1/ })).toBeInTheDocument();
  });

  it("says so when the ticket cannot be prepared", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: { status: 404, message: "Issue not found." },
    });
    render(<TicketPanel orgId="o" issueId="i" />);
    await userEvent.click(screen.getByRole("button", { name: /Prepare ticket/ }));
    expect(await screen.findByText("Issue not found.")).toBeInTheDocument();
  });
});
