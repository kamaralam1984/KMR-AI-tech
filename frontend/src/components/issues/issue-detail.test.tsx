import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Issue } from "@/lib/types";

import { IssueDetail } from "./issue-detail";

const issue = (over: Partial<Issue> = {}): Issue => ({
  id: "i1",
  project_id: "p1",
  scan_id: "s1",
  title: "Checkout script fails",
  category: "frontend_error",
  severity: "high",
  component: "/checkout",
  state: "open",
  classification: "diagnosed",
  confidence: "medium",
  provider: "rules",
  is_ai: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  summary: "A script error appears on checkout.",
  needs_review_reason: null,
  facts: [{ text: "TypeError seen on /checkout", evidence_ids: ["e1"] }],
  hypotheses: [{ text: "A value is missing", confidence: "low", evidence_ids: ["e1"] }],
  unverified: ["Whether customers are affected"],
  reproduction_steps: ["Open /checkout"],
  impact: "Customers may not pay.",
  proposed_fix: "Guard the value.",
  verification_plan: "Rescan.",
  evidence: [{ id: "e1", type: "observation", source: "browser_observed", kind: "console_error" }],
  model: "rules-2",
  ...over,
});

describe("IssueDetail", () => {
  it("keeps observed, inferred and unverified apart and labelled", () => {
    render(<IssueDetail issue={issue()} />);
    expect(screen.getByText("Observed", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Inferred", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Unverified", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("TypeError seen on /checkout")).toBeInTheDocument();
  });

  it("says the built-in rules are not AI", () => {
    render(<IssueDetail issue={issue()} />);
    expect(screen.getByText(/built-in rules, not by AI/i)).toBeInTheDocument();
  });

  it("warns that AI output can be wrong", () => {
    render(<IssueDetail issue={issue({ is_ai: true, model: "claude-x" })} />);
    expect(screen.getByText(/AI can be wrong/i)).toBeInTheDocument();
  });

  it("claims no cause and shows the review reason when needs review", () => {
    render(
      <IssueDetail
        issue={issue({
          classification: "needs_review",
          hypotheses: [],
          needs_review_reason: "Backend logs are missing.",
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Backend logs are missing.");
    expect(screen.getByText(/No cause is suggested/)).toBeInTheDocument();
  });
});
