import Link from "next/link";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  CATEGORY_LABEL,
  CONFIDENCE_LABEL,
  providerNote,
  SOURCE_LABEL,
  STATE_LABEL,
} from "@/lib/issue-text";
import type { Issue } from "@/lib/types";

import { SeverityBadge } from "./severity-badge";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {children}
    </section>
  );
}

/** One issue, with what was observed kept visibly apart from what is only inferred. */
export function IssueDetail({ issue }: { issue: Issue }) {
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={issue.severity} />
            <Badge tone="neutral">{CATEGORY_LABEL[issue.category] ?? issue.category}</Badge>
            <Badge tone="neutral">{CONFIDENCE_LABEL[issue.confidence]}</Badge>
            <Badge tone="neutral">{STATE_LABEL[issue.state] ?? issue.state}</Badge>
          </div>
          <h2 className="text-lg font-semibold break-words text-ink">{issue.title}</h2>
          {issue.component ? <p className="text-sm text-muted">Where: {issue.component}</p> : null}
          {issue.summary ? <p className="text-sm text-ink">{issue.summary}</p> : null}
          <p className="text-xs text-muted">{providerNote(issue.is_ai, issue.model)}</p>
          <p className="text-sm">
            <Link
              href={`/scans/${issue.scan_id}`}
              className="inline-flex min-h-10 items-center font-medium text-primary-text underline underline-offset-2"
            >
              View the scan this came from
            </Link>
          </p>
        </div>
      </Card>

      {issue.classification === "needs_review" ? (
        <Alert tone="warning" title="Needs review">
          {issue.needs_review_reason ??
            "The evidence is incomplete or points in more than one direction, so no cause is claimed."}
        </Alert>
      ) : null}

      <Card
        title="What we know"
        description="Facts come from evidence. Inferences are only guesses."
      >
        <div className="flex flex-col gap-5">
          <Section title="Observed">
            {issue.facts.length === 0 ? (
              <p className="text-sm text-muted">Nothing observed is recorded for this issue.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {issue.facts.map((fact) => (
                  <li key={fact.text} className="flex flex-wrap items-start gap-2 text-sm text-ink">
                    <Badge tone="success">Observed</Badge>
                    <span className="min-w-0 flex-1 break-words">{fact.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="Inferred">
            {issue.hypotheses.length === 0 ? (
              <p className="text-sm text-muted">
                No cause is suggested. The evidence does not support one.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {issue.hypotheses.map((h) => (
                  <li key={h.text} className="flex flex-wrap items-start gap-2 text-sm text-ink">
                    <Badge tone="warning">Inferred</Badge>
                    <span className="min-w-0 flex-1 break-words">
                      {h.text}{" "}
                      <span className="text-muted">({CONFIDENCE_LABEL[h.confidence]})</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="Not yet verified">
            {issue.unverified.length === 0 ? (
              <p className="text-sm text-muted">Nothing is listed as unverified.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {issue.unverified.map((u) => (
                  <li key={u} className="flex flex-wrap items-start gap-2 text-sm text-ink">
                    <Badge tone="neutral">Unverified</Badge>
                    <span className="min-w-0 flex-1 break-words">{u}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </Card>

      <Card title="What to do">
        <div className="flex flex-col gap-5">
          {issue.reproduction_steps.length > 0 ? (
            <Section title="How to see it">
              <ol className="list-decimal pl-5 text-sm text-ink">
                {issue.reproduction_steps.map((step) => (
                  <li key={step} className="py-0.5">
                    {step}
                  </li>
                ))}
              </ol>
            </Section>
          ) : null}
          {issue.impact ? (
            <Section title="Why it matters">
              <p className="text-sm text-ink">{issue.impact}</p>
            </Section>
          ) : null}
          {issue.proposed_fix ? (
            <Section title="Suggested fix">
              <p className="text-sm text-ink">{issue.proposed_fix}</p>
              <p className="text-xs text-muted">
                A suggestion in words only. Nothing is changed in your code.
              </p>
            </Section>
          ) : null}
          {issue.verification_plan ? (
            <Section title="How to check it is fixed">
              <p className="text-sm text-ink">{issue.verification_plan}</p>
            </Section>
          ) : null}
        </div>
      </Card>

      <Card
        title="Evidence"
        description={`${issue.evidence.length} record(s) this issue rests on.`}
      >
        <ul className="flex flex-col gap-2">
          {issue.evidence.map((e) => (
            <li key={e.id} className="text-sm text-ink">
              <strong className="font-medium">{SOURCE_LABEL[e.source] ?? e.source}:</strong>{" "}
              {e.kind.replaceAll("_", " ")}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
