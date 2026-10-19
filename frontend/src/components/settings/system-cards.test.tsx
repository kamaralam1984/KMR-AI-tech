import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SystemData, UsageData } from "@/lib/types";

import { UsageCard, WorkerCard } from "./system-cards";

const system = (over: Partial<SystemData> = {}): SystemData => ({
  database: "ok",
  queue: "connected",
  scans_queued: 0,
  oldest_queued_seconds: null,
  scans_running: 0,
  scans_stalled: 0,
  last_scan_finished_at: null,
  sandbox_docker_cli: true,
  scanner_mode: "container",
  ai_provider: "rules",
  ...over,
});
const usage: UsageData = {
  scans_last_24h: 3,
  scans_daily_limit: 20,
  diagnosis_runs_last_24h: 2,
  diagnosis_daily_limit: 20,
  diagnosis_runs_this_month: 5,
  ai_cost_usd_this_month: null,
  ai_monthly_cost_ceiling_usd: null,
  repair_plans_last_24h: 1,
  repair_plans_daily_limit: 30,
  check_runs_last_hour: 0,
  check_runs_hourly_limit: 20,
};

describe("WorkerCard", () => {
  it("says nothing is wrong only when nothing is wrong", () => {
    render(<WorkerCard system={system()} />);
    expect(screen.getByText("No problem seen")).toBeInTheDocument();
    expect(screen.getByText("Never")).toBeInTheDocument();
    expect(screen.getByText("Built-in rules (not AI)")).toBeInTheDocument();
  });

  it("flags a stalled scan and a long wait in words", () => {
    const { unmount } = render(
      <WorkerCard system={system({ scans_stalled: 1, scans_running: 1 })} />,
    );
    expect(screen.getByText(/stopped reporting/)).toBeInTheDocument();
    unmount();
    render(<WorkerCard system={system({ scans_queued: 2, oldest_queued_seconds: 600 })} />);
    expect(screen.getByText(/Is the worker running/)).toBeInTheDocument();
  });

  it("reports a missing sandbox honestly", () => {
    render(<WorkerCard system={system({ sandbox_docker_cli: false, queue: "not_configured" })} />);
    expect(screen.getByText("Docker not found")).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
  });
});

describe("UsageCard", () => {
  it("shows usage against each limit and an unpriced AI cost as unpriced", () => {
    render(<UsageCard usage={usage} />);
    expect(screen.getByText("3 of 20")).toBeInTheDocument();
    expect(screen.getByText("1 of 30")).toBeInTheDocument();
    expect(screen.getByText("Not priced")).toBeInTheDocument();
  });
});
