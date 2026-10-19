"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";

export function OrgNameForm({
  orgId,
  name,
  canEdit,
}: {
  orgId: string;
  name: string;
  canEdit: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [error, setError] = useState<{ field?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);

  if (!canEdit) {
    return (
      <p className="text-sm text-muted">
        Only owners and admins can rename the organization. Current name:{" "}
        <span className="font-medium text-ink">{name}</span>
      </p>
    );
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = String(new FormData(event.currentTarget).get("name") ?? "").trim();
    if (!next) return setError({ field: "Enter a name." });
    setError({});
    setBusy(true);
    const result = await apiRequest("PATCH", `/organizations/${orgId}`, { name: next });
    setBusy(false);
    if (!result.ok) return setError({ form: result.error.message });
    toast({ tone: "success", title: "Organization renamed" });
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3 sm:flex-row sm:items-end">
      {error.form ? (
        <div className="sm:basis-full">
          <Alert tone="danger" title="Could not rename">
            {error.form}
          </Alert>
        </div>
      ) : null}
      <div className="sm:flex-1">
        <TextField
          key={name}
          label="Organization name"
          name="name"
          defaultValue={name}
          maxLength={100}
          required
          error={error.field}
        />
      </div>
      <Button type="submit" isLoading={busy}>
        Save name
      </Button>
    </form>
  );
}
