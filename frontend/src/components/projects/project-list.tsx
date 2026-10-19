"use client";

import { Pencil } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { TextField } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import type { Project } from "@/lib/types";

const dateFormat = new Intl.DateTimeFormat("en", { dateStyle: "medium" });

export function ProjectList({
  orgId,
  projects,
  canEdit,
}: {
  orgId: string;
  projects: Project[];
  canEdit: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [editing, setEditing] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    const name = String(new FormData(event.currentTarget).get("name") ?? "").trim();
    if (!name) return setError("Enter a project name.");
    setBusy(true);
    const result = await apiRequest<Project>(
      "PATCH",
      `/organizations/${orgId}/projects/${editing.id}`,
      { name },
    );
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setEditing(null);
    toast({ tone: "success", title: "Project renamed" });
    router.refresh();
  }

  return (
    <>
      <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
        {projects.map((project) => (
          <li key={project.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <Link
                href={`/projects/${project.id}`}
                className="block truncate font-medium text-primary-text underline-offset-2 hover:underline"
              >
                {project.name}
              </Link>
              <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
                <span>Created {dateFormat.format(new Date(project.created_at))}</span>
                {project.site.site_host ? (
                  <Badge tone={project.site.verification.verified ? "success" : "warning"}>
                    {project.site.site_host}
                    {project.site.verification.verified ? " · verified" : " · not verified"}
                  </Badge>
                ) : (
                  <Badge tone="neutral">No website yet</Badge>
                )}
              </p>
            </div>
            {canEdit ? (
              <Button
                variant="secondary"
                size="sm"
                aria-label={`Rename ${project.name}`}
                onClick={() => {
                  setError(null);
                  setEditing(project);
                }}
              >
                <Pencil className="size-4" aria-hidden="true" />
                <span className="hidden sm:inline">Rename</span>
              </Button>
            ) : null}
          </li>
        ))}
      </ul>

      <Dialog open={editing !== null} onClose={() => setEditing(null)} title="Rename project">
        <form key={editing?.id} onSubmit={onRename} noValidate className="flex flex-col gap-4">
          {error ? (
            <Alert tone="danger" title="Could not rename">
              {error}
            </Alert>
          ) : null}
          <TextField
            label="Project name"
            name="name"
            defaultValue={editing?.name}
            maxLength={100}
            required
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button type="submit" isLoading={busy}>
              Save
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
