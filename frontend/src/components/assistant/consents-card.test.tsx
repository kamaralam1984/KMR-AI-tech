import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConsentsCard } from "./consents-card";

const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const consent = {
  id: "c1",
  label: "class-7B-roll-14",
  age_band: "12-14",
  consent_basis: "Signed form on file",
  expires_at: "2099-01-01T00:00:00Z",
  revoked: false,
  created_at: "2026-01-01T00:00:00Z",
};

describe("ConsentsCard", () => {
  beforeEach(() => apiRequest.mockReset());

  it("tells the installer that they, not KMR AI, are responsible for collecting consent", () => {
    render(<ConsentsCard orgId="o" installId="i" consents={[]} />);
    expect(screen.getByText(/KMR AI cannot check/)).toBeInTheDocument();
    expect(screen.getByText(/not a child.s name/)).toBeInTheDocument();
    expect(screen.getByText("No consents recorded yet.")).toBeInTheDocument();
  });

  it("shows the token once after recording a consent", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: { token: "kmr_lc_SECRET.abc" } });
    render(<ConsentsCard orgId="o" installId="i" consents={[]} />);
    await userEvent.type(screen.getByLabelText(/Your reference/), "class-7B-roll-14");
    await userEvent.type(screen.getByLabelText(/How you collected/), "Signed form on file");
    await userEvent.click(screen.getByRole("button", { name: /Record consent/ }));
    expect(apiRequest).toHaveBeenCalledWith(
      "POST",
      "/organizations/o/assistant/installs/i/consents",
      expect.objectContaining({ label: "class-7B-roll-14", age_band: "12-14", days: 180 }),
    );
    expect(await screen.findByText("kmr_lc_SECRET.abc")).toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument();
  });

  it("lists consents with their state and offers revoke only while active", () => {
    render(
      <ConsentsCard
        orgId="o"
        installId="i"
        consents={[consent, { ...consent, id: "c2", label: "old", revoked: true }]}
      />,
    );
    expect(screen.getAllByText("Active")).toHaveLength(1);
    expect(screen.getByText("Revoked")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });
});
