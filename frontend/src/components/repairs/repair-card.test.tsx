import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Repair } from "@/lib/types";

import { RepairCard } from "./repair-card";

const repair = (over: Partial<Repair> = {}): Repair => ({
  id: "12345678-aaaa",
  issue_id: "i1",
  project_id: "p1",
  status: "proposed",
  source: "rules",
  provider: "rules",
  model: "repair-rules-1",
  is_ai: false,
  base_branch: "main",
  base_sha: "abcdef0123456789",
  branch_name: "kmr-ai/fix-1",
  explanation: 'Added lang="en".',
  files: [{ path: "index.html", action: "modify", additions: 1, deletions: 1 }],
  diff: '--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-<html>\n+<html lang="en">\n',
  diff_sha256: "f".repeat(64),
  findings: [],
  warnings: [],
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

describe("RepairCard", () => {
  it("shows the exact change, its fingerprint, and that nothing was changed", () => {
    render(<RepairCard repair={repair()} />);
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    expect(screen.getByLabelText("Proposed change")).toHaveTextContent('+<html lang="en">');
    expect(screen.getByText(/SHA-256\): f{64}/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing has been changed in your repository/)).toBeInTheDocument();
    expect(screen.getByText(/not by AI/)).toBeInTheDocument();
  });

  it("explains why a change was refused, in words", () => {
    render(
      <RepairCard
        repair={repair({
          status: "rejected",
          files: [],
          diff: "",
          findings: [{ rule: "adds_secret", message: "x", path: "a.js" }],
        })}
      />,
    );
    expect(screen.getByText("Rejected by safety checks")).toBeInTheDocument();
    expect(screen.getByText(/Would add a credential to the code \(a\.js\)/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Proposed change")).not.toBeInTheDocument();
  });

  it("tells you when an AI wrote it and when a person did", () => {
    const { unmount } = render(<RepairCard repair={repair({ source: "ai", model: "claude-x" })} />);
    expect(screen.getByText(/Written by an AI model \(claude-x\)/)).toBeInTheDocument();
    unmount();
    render(<RepairCard repair={repair({ source: "manual" })} />);
    expect(screen.getByText(/Submitted by a person/)).toBeInTheDocument();
  });
});
