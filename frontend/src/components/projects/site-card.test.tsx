import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { Project } from "@/lib/types";

import { SiteCard } from "./site-card";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh, push: vi.fn(), replace: vi.fn() }),
}));
const apiRequest = vi.fn();
vi.mock("@/lib/client-api", () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }));

const project = (over: Partial<Project["site"]> = {}, verified = false): Project => ({
  id: "p1",
  name: "Shop",
  created_by: "u1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  site: {
    site_url: "https://shop.example.com/",
    site_host: "shop.example.com",
    inject_request_id: true,
    exclusions: ["/admin"],
    max_pages: 10,
    verification: {
      verified,
      method: verified ? "dns" : null,
      verified_at: verified ? "2026-01-02T00:00:00Z" : null,
      dns_record_name: "_kmr-verify.shop.example.com",
      dns_record_value: "kmr-verification=abc123",
      file_url: "https://shop.example.com/.well-known/kmr-verification.txt",
      file_content: "kmr-verification=abc123",
    },
    ...over,
  },
});

const renderCard = (p: Project, canEdit = true) =>
  render(
    <ToastProvider>
      <SiteCard orgId="o1" project={p} canEdit={canEdit} />
    </ToastProvider>,
  );

beforeEach(() => {
  apiRequest.mockReset();
  refresh.mockReset();
});

describe("SiteCard", () => {
  it("shows both ways to prove ownership while unverified", () => {
    renderCard(project());
    expect(screen.getByText("Not verified yet")).toBeInTheDocument();
    expect(screen.getByText("_kmr-verify.shop.example.com")).toBeInTheDocument();
    expect(screen.getAllByText("kmr-verification=abc123")).toHaveLength(2);
    expect(
      screen.getByText("https://shop.example.com/.well-known/kmr-verification.txt"),
    ).toBeInTheDocument();
  });

  it("hides the instructions once verified and says how", () => {
    renderCard(project({}, true));
    expect(screen.getByText(/Verified by DNS/)).toBeInTheDocument();
    expect(screen.queryByText("Option 1: DNS record")).not.toBeInTheDocument();
  });

  it("checks DNS and refreshes when the proof is found", async () => {
    apiRequest.mockResolvedValue({
      ok: true,
      data: { verified: true, reason: null, project: project({}, true) },
    });
    renderCard(project());
    await userEvent.click(screen.getByRole("button", { name: "Check DNS record" }));
    expect(apiRequest).toHaveBeenCalledWith("POST", "/organizations/o1/projects/p1/verify", {
      method: "dns",
    });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("explains a failed check instead of pretending it worked", async () => {
    apiRequest.mockResolvedValue({
      ok: true,
      data: {
        verified: false,
        reason: "The DNS record was not found or does not match yet.",
        project: project(),
      },
    });
    renderCard(project());
    await userEvent.click(screen.getByRole("button", { name: "Check file" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/not found or does not match/i)).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("shows a refused address as an error on the address field", async () => {
    apiRequest.mockResolvedValue({
      ok: false,
      error: {
        status: 422,
        message: "That address points to a private network, which cannot be scanned.",
        fieldErrors: {},
      },
    });
    renderCard(project({ site_url: null, site_host: null }));
    await userEvent.type(screen.getByLabelText(/Website address/), "http://10.0.0.1/");
    await userEvent.click(screen.getByRole("button", { name: "Save website" }));
    expect(await screen.findByText(/private network/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Website address/)).toHaveAttribute("aria-invalid", "true");
  });

  it("sends exclusions one per line and the page limit when saving", async () => {
    apiRequest.mockResolvedValue({ ok: true, data: project() });
    renderCard(project());
    await userEvent.click(screen.getByRole("button", { name: "Save website" }));
    expect(apiRequest).toHaveBeenCalledWith("PUT", "/organizations/o1/projects/p1/site", {
      site_url: "https://shop.example.com/",
      exclusions: ["/admin"],
      max_pages: 10,
      inject_request_id: true,
    });
  });

  it("gives view-only users the facts but no way to change or check anything", () => {
    renderCard(project(), false);
    expect(screen.queryByRole("button", { name: "Save website" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check DNS record" })).not.toBeInTheDocument();
    expect(screen.getByText(/Website: https:\/\/shop.example.com/)).toBeInTheDocument();
  });
});
