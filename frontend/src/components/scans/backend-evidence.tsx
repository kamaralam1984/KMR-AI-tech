"use client";

import { Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { SelectField } from "@/components/ui/select";
import { TextAreaField } from "@/components/ui/textarea-field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { atLeast } from "@/lib/org";
import type { BackendEvidence as Evidence, Correlation, Role, ScanStatus } from "@/lib/types";

const CLASS_LABEL: Record<Correlation["classification"], { text: string; tone: Tone }> = {
  backend_confirmed: { text: "Seen on both sides", tone: "danger" },
  uncertain: { text: "Needs review", tone: "warning" },
  no_backend_data: { text: "Needs review: no backend data", tone: "warning" },
  third_party: { text: "Another website", tone: "neutral" },
};
const SOURCE_LABEL: Record<string, string> = {
  browser_observed: "Observed in the browser",
  api_observed: "Observed by an API check",
  customer_supplied: "Supplied by you",
  repository_derived: "Derived from your code",
};
const STATE_LABEL: Record<string, { text: string; tone: Tone }> = {
  available: { text: "Available", tone: "success" },
  no_data: { text: "No data", tone: "warning" },
  not_connected: { text: "Not connected", tone: "neutral" },
};

export function BackendEvidence({
  orgId,
  scanId,
  role,
  status,
}: {
  orgId: string;
  scanId: string;
  role: Role;
  status: ScanStatus;
}) {
  const toast = useToast();
  const base = `/organizations/${orgId}/scans/${scanId}`;
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<"jsonl" | "text">("jsonl");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [reloadKey, setReloadKey] = useState(0);

  // Loads on mount, whenever the scan changes state (for example when it finishes), and after the
  // logs are changed. The result is applied only if this effect has not been superseded.
  useEffect(() => {
    let cancelled = false;
    apiRequest<Evidence>("GET", `${base}/backend-evidence`).then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setEvidence(result.data);
        setFailed(false);
      } else {
        setFailed(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [base, status, reloadKey]);

  async function upload() {
    if (!content.trim()) return setError("Paste some log lines first.");
    setBusy(true);
    setError(null);
    const result = await apiRequest<{ accepted: number; rejected: number }>(
      "POST",
      `${base}/backend-logs`,
      { format, content },
    );
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setOpen(false);
    setContent("");
    toast({
      tone: result.data.accepted > 0 ? "success" : "warning",
      title: `${result.data.accepted} log line${result.data.accepted === 1 ? "" : "s"} added`,
      description:
        result.data.rejected > 0 ? `${result.data.rejected} could not be used.` : undefined,
    });
    setReloadKey((n) => n + 1);
  }

  async function removeLogs() {
    const result = await apiRequest("DELETE", `${base}/backend-logs`);
    if (result.ok) {
      toast({ tone: "success", title: "Uploaded logs removed" });
      setReloadKey((n) => n + 1);
    } else {
      toast({ tone: "danger", title: "Could not remove", description: result.error.message });
    }
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 1_000_000)
      return setError("That file is larger than 1 MB. Upload a smaller excerpt.");
    setContent(await file.text());
    setFormat(file.name.endsWith(".jsonl") || file.name.endsWith(".json") ? "jsonl" : "text");
  }

  const canUpload = atLeast(role, "member");
  const logsAvailable =
    evidence?.sources.find((s) => s.key === "customer_logs")?.state === "available";

  return (
    <Card
      title="Backend evidence"
      description="Browser failures compared with what your backend shows. Causes are not guessed."
    >
      {failed && !evidence ? (
        <Alert tone="warning" title="Could not load backend evidence">
          Reload the page to try again.
        </Alert>
      ) : null}
      {evidence ? (
        <div className="flex flex-col gap-5">
          {evidence.notice ? (
            <Alert tone="warning" title="No backend telemetry">
              {evidence.notice}
            </Alert>
          ) : null}

          <ul
            aria-label="Evidence sources"
            className="divide-y divide-line rounded-lg border border-line"
          >
            {evidence.sources.map((source) => (
              <li
                key={source.key}
                className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{source.label}</p>
                  <p className="text-sm text-muted">{source.detail}</p>
                </div>
                <Badge tone={STATE_LABEL[source.state].tone}>
                  {STATE_LABEL[source.state].text}
                </Badge>
              </li>
            ))}
          </ul>

          {canUpload ? (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setOpen(true)}>
                <Upload className="size-4" aria-hidden="true" />
                Upload backend logs
              </Button>
              {logsAvailable ? (
                <Button variant="ghost" onClick={removeLogs}>
                  Remove uploaded logs
                </Button>
              ) : null}
            </div>
          ) : null}

          {evidence.correlations.length === 0 ? (
            <p className="text-sm text-muted">
              No failing requests were found to compare with backend evidence.
            </p>
          ) : (
            <ul aria-label="Browser failures and backend evidence" className="flex flex-col gap-3">
              {evidence.correlations.map((c) => (
                <li key={c.observation_id} className="rounded-lg border border-line p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      tone={c.kind === "api_check" ? "danger" : CLASS_LABEL[c.classification].tone}
                    >
                      {c.kind === "api_check"
                        ? "API check failed"
                        : CLASS_LABEL[c.classification].text}
                    </Badge>
                    {c.http_status ? (
                      <span className="text-sm text-muted">HTTP {c.http_status}</span>
                    ) : null}
                  </div>
                  {c.request_url ? (
                    <p className="mt-2 text-sm font-medium break-all text-ink">{c.request_url}</p>
                  ) : null}
                  <p className="mt-1 text-sm text-ink">{c.statement}</p>
                  <ul className="mt-2 flex flex-col gap-1">
                    {c.evidence.map((e) => (
                      <li key={`${e.ref_type}-${e.ref_id}`} className="text-xs text-muted">
                        <strong className="font-medium text-ink">{SOURCE_LABEL[e.source]}:</strong>{" "}
                        {e.summary}
                      </li>
                    ))}
                  </ul>
                  {c.request_id ? (
                    <p className="mt-2 text-xs text-muted">
                      Request ID <code>{c.request_id}</code>
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : !failed ? (
        <p role="status" className="text-sm text-muted">
          Loading backend evidence…
        </p>
      ) : null}

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Upload backend logs"
        description="Paste lines from your server logs. Each needs a request ID or a path. Passwords, tokens and emails are masked before storage."
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={upload} isLoading={busy}>
              Add logs
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {error ? (
            <Alert tone="danger" title="Could not add the logs">
              {error}
            </Alert>
          ) : null}
          <SelectField
            label="Format"
            value={format}
            onChange={(event) => setFormat(event.target.value as "jsonl" | "text")}
          >
            <option value="jsonl">JSON lines (one JSON object per line)</option>
            <option value="text">Plain text</option>
          </SelectField>
          <TextAreaField
            label="Log lines"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            spellCheck={false}
            hint="Up to 1 MB. Requests are matched by the X-Request-ID header the scanner sent."
          />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="log-file" className="text-sm font-medium text-ink">
              Or choose a file
            </label>
            <input
              id="log-file"
              type="file"
              accept=".log,.txt,.json,.jsonl"
              onChange={(event) => void readFile(event.target.files?.[0])}
              className="text-sm"
            />
          </div>
        </div>
      </Dialog>
    </Card>
  );
}
