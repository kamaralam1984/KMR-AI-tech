"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { ORG_COOKIE } from "@/lib/org";
import type { Org } from "@/lib/types";

export function CreateOrgForm({ submitLabel = "Create organization" }: { submitLabel?: string }) {
  const router = useRouter();
  const toast = useToast();
  const [error, setError] = useState<{ field?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!name) return setError({ field: "Enter a name for your organization." });
    setError({});
    setBusy(true);
    const result = await apiRequest<Org>("POST", "/organizations", { name });
    setBusy(false);
    if (!result.ok) return setError({ form: result.error.message });
    const secure = window.location.protocol === "https:" ? "; secure" : "";
    document.cookie = `${ORG_COOKIE}=${encodeURIComponent(result.data.id)}; path=/; max-age=31536000; samesite=lax${secure}`;
    form.reset();
    toast({ tone: "success", title: "Organization created", description: result.data.name });
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {error.form ? (
        <Alert tone="danger" title="Could not create the organization">
          {error.form}
        </Alert>
      ) : null}
      <TextField
        label="Organization name"
        name="name"
        maxLength={100}
        required
        placeholder="Acme Inc."
        error={error.field}
      />
      <Button type="submit" isLoading={busy} className="self-start">
        {submitLabel}
      </Button>
    </form>
  );
}
