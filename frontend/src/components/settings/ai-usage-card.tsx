import { Card } from "@/components/ui/card";
import { providerNote } from "@/lib/issue-text";
import type { AiUsage } from "@/lib/types";

export function AiUsageCard({ usage }: { usage: AiUsage }) {
  return (
    <Card title="Diagnosis" description={usage.label}>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted">Runs today</dt>
          <dd className="font-medium text-ink">
            {usage.daily_runs_used} of {usage.daily_runs_limit}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Runs this month</dt>
          <dd className="font-medium text-ink">{usage.month_runs}</dd>
        </div>
        <div>
          <dt className="text-muted">Cost this month</dt>
          <dd className="font-medium text-ink">
            {usage.month_cost_usd === null ? "Not priced" : `$${usage.month_cost_usd.toFixed(4)}`}
            {usage.monthly_cost_ceiling_usd !== null
              ? ` of $${usage.monthly_cost_ceiling_usd.toFixed(2)} limit`
              : ""}
          </dd>
        </div>
      </dl>
      <p className="mt-4 text-xs text-muted">{providerNote(usage.is_ai, usage.model)}</p>
    </Card>
  );
}
