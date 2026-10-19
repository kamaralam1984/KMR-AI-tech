"use client";

import { useRouter } from "next/navigation";
import { type ChangeEvent, type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { TextAreaField } from "@/components/ui/textarea-field";
import { apiRequest } from "@/lib/client-api";
import type { TrafficSummary } from "@/lib/types";

const MAX_FILE_BYTES = 400_000;

/**
 * Uploads page views per path exported from the owner's own analytics. They only weight the
 * ranking of issues; KMR AI does not verify them and shows no revenue or conversion figure.
 */
export function TrafficCard({
  orgId,
  projectId,
  traffic,
  canEdit,
}: {
  orgId: string;
  projectId: string;
  traffic: TrafficSummary;
  canEdit: boolean;
}) {
  const router = useRouter();
  const base = `/organizations/${orgId}/projects/${projectId}/traffic`;
  const [csv, setCsv] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejected, setRejected] = useState<number | null>(null);

  async function readFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) return setError("That file is too large (400 KB at most).");
    setError(null);
    setCsv(await file.text());
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    const result = await apiRequest<TrafficSummary>("PUT", base, {
      source_label: String(data.get("source_label") ?? "").trim(),
      period_start: String(data.get("period_start") ?? ""),
      period_end: String(data.get("period_end") ?? ""),
      csv,
    });
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setRejected(result.data.rows_rejected);
    setCsv("");
    router.refresh();
  }

  async function remove() {
    if (!window.confirm("Delete the uploaded traffic? Rankings go back to the page-type estimate."))
      return;
    const result = await apiRequest("DELETE", base);
    if (!result.ok) return setError(result.error.message);
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">{traffic.notice}</p>
      {traffic.present ? (
        <div className="rounded-md border border-line p-3 text-sm">
          <p className="font-medium text-ink">
            {traffic.source_label} · {traffic.period_start} to {traffic.period_end}
          </p>
          <p className="text-muted">
            {traffic.total_views.toLocaleString()} views across {traffic.pages} pages
          </p>
          <ul className="mt-2 text-xs text-muted">
            {traffic.top_pages.slice(0, 5).map((p) => (
              <li key={p.path} className="break-all">
                {p.path}: {p.views.toLocaleString()} ({p.share_percent}%)
              </li>
            ))}
          </ul>
          {canEdit ? (
            <div className="mt-3">
              <Button variant="secondary" size="sm" onClick={remove}>
                Delete uploaded traffic
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted">
          No traffic uploaded. Issues are ranked by severity and the kind of page only.
        </p>
      )}
      {error ? (
        <Alert tone="danger" title="Could not save the traffic">
          {error}
        </Alert>
      ) : null}
      {rejected ? (
        <Alert tone="info" title="Saved">
          {rejected} row(s) could not be read and were skipped.
        </Alert>
      ) : null}
      {canEdit ? (
        <form onSubmit={save} noValidate className="flex flex-col gap-3">
          <TextField
            label="Where the numbers come from"
            name="source_label"
            maxLength={100}
            placeholder="Analytics export"
            required
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="Period start" name="period_start" type="date" required />
            <TextField label="Period end" name="period_end" type="date" required />
          </div>
          <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
            CSV file
            <input type="file" accept=".csv,text/csv,text/plain" onChange={readFile} />
            <span className="text-xs font-normal text-muted">
              A header row with a path column (path, page, url) and a views column (views,
              pageviews, sessions). Other columns are ignored.
            </span>
          </label>
          <TextAreaField
            label="Or paste the CSV"
            name="csv_text"
            value={csv}
            onChange={(e) => setCsv(e.target.value)}
            rows={4}
            spellCheck={false}
          />
          <div>
            <Button type="submit" variant="secondary" isLoading={busy} disabled={!csv.trim()}>
              Upload traffic
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
