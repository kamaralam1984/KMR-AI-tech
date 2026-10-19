"use client";

import { Pencil, Play, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { TextField } from "@/components/ui/field";
import { TextAreaField } from "@/components/ui/textarea-field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { reasonsText } from "@/lib/scan-text";
import type { ApiCheck, ApiCheckRun } from "@/lib/types";

const RUN_TONE: Record<string, Tone> = { passed: "success", failed: "danger", error: "warning" };
const RUN_LABEL: Record<string, string> = {
  passed: "Passed",
  failed: "Failed",
  error: "Could not run",
};

function CheckForm({
  initial,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  initial: ApiCheck | null;
  busy: boolean;
  error: string | null;
  onSubmit: (body: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [formError, setFormError] = useState<string | null>(null);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const schemaText = String(data.get("schema") ?? "").trim();
    let schema: unknown = undefined;
    if (schemaText) {
      try {
        schema = JSON.parse(schemaText);
      } catch {
        return setFormError("The expected response shape is not valid JSON.");
      }
    }
    setFormError(null);
    const headerName = String(data.get("header_name") ?? "").trim();
    const secret = String(data.get("credential") ?? "");
    const body: Record<string, unknown> = {
      name: String(data.get("name") ?? "").trim(),
      path: String(data.get("path") ?? "").trim(),
      expected_status: Number(data.get("expected_status")),
      max_latency_ms: Number(data.get("max_latency_ms")),
    };
    if (schema !== undefined) body.response_schema = schema;
    else if (initial?.response_schema) body.clear_schema = true;
    if (headerName && secret) body.credentials = { header_name: headerName, value: secret };
    if (initial?.has_credentials && data.get("remove_credentials")) body.clear_credentials = true;
    onSubmit(body);
  }

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      {formError || error ? (
        <Alert tone="danger" title="Could not save the check">
          {formError ?? error}
        </Alert>
      ) : null}
      <TextField label="Name" name="name" defaultValue={initial?.name} maxLength={100} required />
      <TextField
        label="Path on your website"
        name="path"
        defaultValue={initial?.path ?? "/api/"}
        hint="Starts with /. Only GET requests to your verified website are made."
        autoCapitalize="none"
        required
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Expected status"
          name="expected_status"
          type="number"
          inputMode="numeric"
          min={100}
          max={599}
          defaultValue={initial?.expected_status ?? 200}
        />
        <TextField
          label="Slowest acceptable (ms)"
          name="max_latency_ms"
          type="number"
          inputMode="numeric"
          min={50}
          max={30000}
          defaultValue={initial?.max_latency_ms ?? 3000}
        />
      </div>
      <TextAreaField
        label="Expected response shape (JSON Schema, optional)"
        name="schema"
        defaultValue={
          initial?.response_schema ? JSON.stringify(initial.response_schema, null, 2) : ""
        }
        placeholder={'{"type": "object", "required": ["id"]}'}
        hint="Checked against the JSON the API returns. The response itself is never stored."
        spellCheck={false}
      />
      <fieldset className="flex flex-col gap-3 rounded-lg border border-line p-4">
        <legend className="px-1 text-sm font-medium text-ink">Credentials (optional)</legend>
        <p className="text-xs text-muted">
          For endpoints that need a key. It is stored encrypted, sent only with this check, and can
          never be shown again.
        </p>
        {initial?.has_credentials ? (
          <p className="text-sm text-ink">
            A credential is stored for header <code>{initial.auth_header_name}</code>. Leave the
            fields empty to keep it, or enter a new one to replace it.
          </p>
        ) : null}
        <TextField
          label="Header name"
          name="header_name"
          placeholder="Authorization"
          autoCapitalize="none"
          autoComplete="off"
        />
        <TextField
          label="Header value"
          name="credential"
          type="password"
          placeholder="Bearer …"
          autoComplete="off"
        />
        {initial?.has_credentials ? (
          <label className="flex min-h-6 items-center gap-2 text-sm text-ink">
            <input type="checkbox" name="remove_credentials" className="size-5 accent-primary" />
            Remove the stored credential
          </label>
        ) : null}
      </fieldset>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" isLoading={busy}>
          {initial ? "Save check" : "Add check"}
        </Button>
      </div>
    </form>
  );
}

export function ApiChecksCard({
  orgId,
  projectId,
  checks,
  canWrite,
  verified,
}: {
  orgId: string;
  projectId: string;
  checks: ApiCheck[];
  canWrite: boolean;
  verified: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const base = `/organizations/${orgId}/projects/${projectId}/api-checks`;
  const [editing, setEditing] = useState<ApiCheck | "new" | null>(null);
  const [deleting, setDeleting] = useState<ApiCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ApiCheckRun>>({});

  async function save(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    const result =
      editing === "new" || editing === null
        ? await apiRequest<ApiCheck>("POST", base, body)
        : await apiRequest<ApiCheck>("PATCH", `${base}/${editing.id}`, body);
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setEditing(null);
    toast({ tone: "success", title: "API check saved" });
    router.refresh();
  }

  async function run(check: ApiCheck) {
    setRunning(check.id);
    const result = await apiRequest<ApiCheckRun>("POST", `${base}/${check.id}/run`);
    setRunning(null);
    if (!result.ok) {
      toast({
        tone: "danger",
        title: "Could not run the check",
        description: result.error.message,
      });
      return;
    }
    setResults((current) => ({ ...current, [check.id]: result.data }));
    router.refresh();
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    const result = await apiRequest("DELETE", `${base}/${deleting.id}`);
    setBusy(false);
    setDeleting(null);
    if (result.ok) {
      toast({ tone: "success", title: "API check removed" });
      router.refresh();
    } else {
      toast({ tone: "danger", title: "Could not remove", description: result.error.message });
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">
        An API check calls one endpoint on your verified website (read-only) and compares the answer
        with what you expect. It runs on demand and at the end of every scan, so a page failure can
        be compared with what the API did.
      </p>
      {checks.length === 0 ? (
        <p className="text-sm text-ink">No API checks yet.</p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
          {checks.map((check) => {
            const last = results[check.id] ?? check.last_run;
            return (
              <li key={check.id} className="flex flex-col gap-2 px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-medium text-ink">{check.name}</p>
                    <p className="text-sm break-all text-muted">
                      {check.method} {check.path} · expects {check.expected_status}, under{" "}
                      {check.max_latency_ms} ms
                      {check.has_credentials ? ` · uses ${check.auth_header_name}` : ""}
                    </p>
                  </div>
                  {last ? (
                    <Badge tone={RUN_TONE[last.status]}>{RUN_LABEL[last.status]}</Badge>
                  ) : (
                    <Badge tone="neutral">Not run yet</Badge>
                  )}
                </div>
                {last && last.status !== "passed" ? (
                  <p role="status" className="text-sm text-ink">
                    {reasonsText(last.reasons)}
                  </p>
                ) : null}
                {canWrite ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => run(check)}
                      isLoading={running === check.id}
                      disabled={!verified || running !== null}
                      aria-label={`Run ${check.name} now`}
                    >
                      <Play className="size-4" aria-hidden="true" />
                      Run now
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setError(null);
                        setEditing(check);
                      }}
                      aria-label={`Edit ${check.name}`}
                    >
                      <Pencil className="size-4" aria-hidden="true" />
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setDeleting(check)}
                      aria-label={`Remove ${check.name}`}
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                      Remove
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {!verified && checks.length > 0 ? (
        <p className="text-sm text-muted">Verify your website to run checks.</p>
      ) : null}
      {canWrite ? (
        <div>
          <Button
            variant="secondary"
            onClick={() => {
              setError(null);
              setEditing("new");
            }}
          >
            Add an API check
          </Button>
        </div>
      ) : null}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add an API check" : "Edit API check"}
      >
        {editing !== null ? (
          <CheckForm
            key={editing === "new" ? "new" : editing.id}
            initial={editing === "new" ? null : editing}
            busy={busy}
            error={error}
            onSubmit={save}
            onCancel={() => setEditing(null)}
          />
        ) : null}
      </Dialog>
      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title="Remove this API check?"
        description={deleting ? `“${deleting.name}” and its history will be deleted.` : undefined}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              Keep it
            </Button>
            <Button variant="danger" onClick={remove} isLoading={busy}>
              Remove
            </Button>
          </>
        }
      />
    </div>
  );
}
