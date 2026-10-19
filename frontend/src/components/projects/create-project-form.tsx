"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import type { Project } from "@/lib/types";

export function CreateProjectForm({ orgId }: { orgId: string }) {
  const router = useRouter();
  const toast = useToast();
  const [error, setError] = useState<{ field?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!name) return setError({ field: "Enter a project name." });
    setError({});
    setBusy(true);
    const result = await apiRequest<Project>("POST", `/organizations/${orgId}/projects`, { name });
    setBusy(false);
    if (!result.ok) return setError({ form: result.error.message });
    form.reset();
    toast({ tone: "success", title: "Project created", description: result.data.name });
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3 sm:flex-row sm:items-end">
      {error.form ? (
        <div className="sm:basis-full">
          <Alert tone="danger" title="Could not create the project">
            {error.form}
          </Alert>
        </div>
      ) : null}
      <div className="sm:flex-1">
        <TextField label="Project name" name="name" maxLength={100} required error={error.field} />
      </div>
      <Button type="submit" isLoading={busy}>
        Create project
      </Button>
    </form>
  );
}
