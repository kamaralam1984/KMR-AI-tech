"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { SelectField } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import type { Repository } from "@/lib/types";

const PROVIDER_LABEL = { local_git: "Local git folder", github: "GitHub" } as const;

export function RepositoryCard({
  orgId,
  projectId,
  repository,
  canEdit,
}: {
  orgId: string;
  projectId: string;
  repository: Repository | null;
  canEdit: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [provider, setProvider] = useState<"local_git" | "github">(
    repository?.provider ?? "local_git",
  );
  const [editing, setEditing] = useState(repository === null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `/organizations/${orgId}/projects/${projectId}/repository`;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const token = String(data.get("token") ?? "");
    const command = String(data.get("check_command") ?? "").trim();
    const body: Record<string, unknown> = {
      provider,
      location: String(data.get("location") ?? "").trim(),
      default_branch: String(data.get("default_branch") ?? "").trim() || "main",
    };
    if (token) body.token = token;
    if (command) body.check_command = command;
    else if (repository?.check_command) body.clear_check_command = true;
    setBusy(true);
    setError(null);
    const result = await apiRequest<Repository>("PUT", base, body);
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setEditing(false);
    toast({ tone: "success", title: "Repository connected" });
    router.refresh();
  }

  async function disconnect() {
    setBusy(true);
    const result = await apiRequest("DELETE", base);
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setEditing(true);
    toast({ tone: "success", title: "Repository disconnected" });
    router.refresh();
  }

  if (repository && !editing) {
    return (
      <div className="flex flex-col gap-3">
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted">Type</dt>
            <dd className="font-medium text-ink">{PROVIDER_LABEL[repository.provider]}</dd>
          </div>
          <div>
            <dt className="text-muted">Location</dt>
            <dd className="font-medium break-all text-ink">{repository.location}</dd>
          </div>
          <div>
            <dt className="text-muted">Default branch (never changed by KMR AI)</dt>
            <dd className="font-medium text-ink">{repository.default_branch}</dd>
          </div>
          <div>
            <dt className="text-muted">Access token</dt>
            <dd>
              {repository.has_token ? (
                <Badge tone="success">Stored encrypted</Badge>
              ) : (
                <Badge tone="neutral">None</Badge>
              )}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-muted">Project check command</dt>
            <dd className="font-medium break-all text-ink">
              {repository.check_command ?? "None: the project's own tests are not run."}
            </dd>
          </div>
        </dl>
        {error ? (
          <Alert tone="danger" title="Could not update">
            {error}
          </Alert>
        ) : null}
        {canEdit ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit
            </Button>
            <Button variant="secondary" onClick={disconnect} isLoading={busy}>
              Disconnect
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  if (!canEdit) {
    return (
      <p className="text-sm text-muted">No repository is connected. An admin can connect one.</p>
    );
  }
  return (
    <form onSubmit={save} noValidate className="flex flex-col gap-4">
      {error ? (
        <Alert tone="danger" title="Could not connect the repository">
          {error}
        </Alert>
      ) : null}
      <SelectField
        label="Repository type"
        value={provider}
        onChange={(e) => setProvider(e.target.value as "local_git" | "github")}
      >
        <option value="local_git">Local git folder (this server)</option>
        <option value="github">GitHub</option>
      </SelectField>
      <TextField
        label={provider === "github" ? "Repository (owner/name)" : "Folder path"}
        name="location"
        defaultValue={repository?.location}
        hint={
          provider === "github"
            ? "For example acme/website."
            : "Must be inside a folder the server administrator allows."
        }
        autoCapitalize="none"
        spellCheck={false}
        required
      />
      <TextField
        label="Default branch"
        name="default_branch"
        defaultValue={repository?.default_branch ?? "main"}
        autoCapitalize="none"
      />
      {provider === "github" ? (
        <TextField
          label="Access token"
          name="token"
          type="password"
          autoComplete="off"
          hint={
            repository?.has_token
              ? "Leave empty to keep the stored token. It can never be shown again."
              : "Stored encrypted and never shown again. Give it only the access it needs."
          }
        />
      ) : null}
      <TextField
        label="Project check command (optional)"
        name="check_command"
        defaultValue={repository?.check_command ?? ""}
        hint="Run on a copy of the change inside a sandbox with no network access."
        autoCapitalize="none"
        spellCheck={false}
      />
      <div className="flex gap-2">
        <Button type="submit" isLoading={busy}>
          Connect repository
        </Button>
        {repository ? (
          <Button type="button" variant="secondary" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
