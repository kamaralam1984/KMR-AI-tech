import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExtensionTokensCard } from "./extension-tokens-card";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const token = {
  id: "t1",
  name: "Laptop",
  token_prefix: "kmr_xt_ab12",
  kind: "extension" as const,
  user_id: "u1",
  expires_at: "2099-01-01T00:00:00Z",
  revoked: false,
  last_used_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

describe("ExtensionTokensCard", () => {
  beforeEach(() => apiRequest.mockReset());

  it("lists tokens without ever showing a secret, and says when one was never used", () => {
    render(<ExtensionTokensCard orgId="o" tokens={[token]} />);
    expect(screen.getByText("Laptop")).toBeInTheDocument();
    expect(screen.getByText(/kmr_xt_ab12… · expires/)).toBeInTheDocument();
    expect(screen.getByText(/never used/)).toBeInTheDocument();
  });

  it("shows a new token once, with a clear warning", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { token: "kmr_xt_SECRETVALUE" } });
    render(<ExtensionTokensCard orgId="o" tokens={[]} />);
    await userEvent.type(screen.getByLabelText(/Token name/), "Work PC");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o/extension-tokens", {
      name: "Work PC",
      days: 30,
      kind: "extension",
    });
    expect(await screen.findByText("kmr_xt_SECRETVALUE")).toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument();
  });

  it("can create a token for the desktop assistant, and says which app each token is for", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { token: "kmr_dt_VALUE" } });
    render(<ExtensionTokensCard orgId="o" tokens={[{ ...token, kind: "desktop" }]} />);
    expect(screen.getByText(/Desktop assistant · kmr_xt_ab12/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Token name/), "Laptop app");
    await userEvent.selectOptions(screen.getByLabelText("For"), "desktop");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o/extension-tokens", {
      name: "Laptop app",
      days: 30,
      kind: "desktop",
    });
    expect(await screen.findByText("kmr_dt_VALUE")).toBeInTheDocument();
  });

  it("explains a refusal in plain words", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: { status: 409, message: "You already have 5 active tokens." },
    });
    render(<ExtensionTokensCard orgId="o" tokens={[]} />);
    await userEvent.type(screen.getByLabelText(/Token name/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByText("You already have 5 active tokens.")).toBeInTheDocument();
  });
});
