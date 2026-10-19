import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { SystemData, UsageData } from "@/lib/types";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="font-medium text-ink">{value}</dd>
    </div>
  );
}

export function UsageCard({ usage }: { usage: UsageData }) {
  return (
    <Card
      title="Usage and limits"
      description="What your organization has used, against its limits."
    >
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <Row
          label="Scans, last 24 hours"
          value={`${usage.scans_last_24h} of ${usage.scans_daily_limit}`}
        />
        <Row
          label="Diagnoses, last 24 hours"
          value={`${usage.diagnosis_runs_last_24h} of ${usage.diagnosis_daily_limit}`}
        />
        <Row label="Diagnoses this month" value={String(usage.diagnosis_runs_this_month)} />
        <Row
          label="Repair plans, last 24 hours"
          value={`${usage.repair_plans_last_24h} of ${usage.repair_plans_daily_limit}`}
        />
        <Row
          label="Check runs, last hour"
          value={`${usage.check_runs_last_hour} of ${usage.check_runs_hourly_limit}`}
        />
        <Row
          label="AI cost this month"
          value={
            usage.ai_cost_usd_this_month === null
              ? "Not priced"
              : `$${usage.ai_cost_usd_this_month.toFixed(4)}${
                  usage.ai_monthly_cost_ceiling_usd !== null
                    ? ` of $${usage.ai_monthly_cost_ceiling_usd.toFixed(2)} limit`
                    : ""
                }`
          }
        />
      </dl>
    </Card>
  );
}

export function WorkerCard({ system }: { system: SystemData }) {
  const stalled = system.scans_stalled > 0;
  const waiting = system.oldest_queued_seconds !== null && system.oldest_queued_seconds > 120;
  return (
    <Card title="Scan worker" description="Health of your own scans in the queue.">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted">Status</dt>
          <dd className="mt-1">
            {stalled ? (
              <Badge tone="danger">A running scan has stopped reporting</Badge>
            ) : waiting ? (
              <Badge tone="warning">Scans are waiting. Is the worker running?</Badge>
            ) : (
              <Badge tone="success">No problem seen</Badge>
            )}
          </dd>
        </div>
        <Row label="Queued" value={String(system.scans_queued)} />
        <Row label="Running" value={String(system.scans_running)} />
        <Row
          label="Last scan finished"
          value={
            system.last_scan_finished_at
              ? new Date(system.last_scan_finished_at).toLocaleString()
              : "Never"
          }
        />
        <Row
          label="Job queue"
          value={system.queue === "connected" ? "Connected" : "Not configured"}
        />
        <Row
          label="Check sandbox"
          value={system.sandbox_docker_cli ? "Docker available" : "Docker not found"}
        />
        <Row
          label="Diagnosis provider"
          value={system.ai_provider === "rules" ? "Built-in rules (not AI)" : "AI model"}
        />
      </dl>
    </Card>
  );
}
