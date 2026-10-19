import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SystemStatus } from "./system-status";

const fetchReadiness = vi.fn();
vi.mock("@/lib/api", () => ({ fetchReadiness: () => fetchReadiness() }));

beforeEach(() => fetchReadiness.mockReset());

describe("SystemStatus", () => {
  it("shows an error state when the API cannot be reached", async () => {
    fetchReadiness.mockResolvedValue({ kind: "unreachable" });
    render(await SystemStatus());
    expect(screen.getByRole("alert")).toHaveTextContent(/cannot reach the kmr ai api/i);
  });

  it("shows each dependency with a text status", async () => {
    fetchReadiness.mockResolvedValue({
      kind: "ok",
      readiness: {
        status: "not_ready",
        checks: { database: "reachable", redis: "unreachable" },
      },
    });
    render(await SystemStatus());
    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Reachable")).toBeInTheDocument();
    expect(screen.getByText("Unreachable")).toBeInTheDocument();
  });

  it("reports unconfigured dependencies as such, not as healthy", async () => {
    fetchReadiness.mockResolvedValue({
      kind: "ok",
      readiness: { status: "ready", checks: { database: "not_configured" } },
    });
    render(await SystemStatus());
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.queryByText("Reachable")).not.toBeInTheDocument();
  });
});
