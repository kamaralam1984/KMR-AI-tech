"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import { TextField } from "@/components/ui/field";
import { SelectField } from "@/components/ui/select";
import { apiRequest } from "@/lib/client-api";
import type { ExtensionToken } from "@/lib/types";

/** Tokens for the Chrome page agent. A token is shown once; only its hash is stored. */
export function ExtensionTokensCard({
  orgId,
  tokens,
}: {
  orgId: string;
  tokens: ExtensionToken[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const base = `/organizations/${orgId}/extension-tokens`;

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError(null);
    const result = await apiRequest<{ token: string }>("POST", base, {
      name: String(data.get("name") ?? "").trim(),
      days: Number(data.get("days") ?? 30),
      kind: String(data.get("kind") ?? "extension"),
    });
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setShown(result.data.token);
    form.reset();
    router.refresh();
  }

  async function revoke(id: string) {
    if (!window.confirm("Revoke this token? The extension using it stops working at once.")) return;
    const result = await apiRequest("POST", `${base}/${id}/revoke`);
    if (!result.ok) return setError(result.error.message);
    router.refresh();
  }

  return (
    <Card
      title="Browser extension and desktop assistant"
      description="Tokens for the Chrome page agent (reads a page you turn it on for, only verified websites) and for the desktop assistant app (reads only the windows you choose when you press Look now). Both redact personal details on your computer first, and a token works only for the app it was made for."
    >
      <div className="flex flex-col gap-4">
        {error ? (
          <Alert tone="danger" title="That did not work">
            {error}
          </Alert>
        ) : null}
        {shown ? (
          <Alert tone="warning" title="Copy this token now. It will not be shown again.">
            <p className="font-mono text-xs break-all">{shown}</p>
            <div className="mt-3">
              <CopyButton value={shown} label="extension token" />
            </div>
          </Alert>
        ) : null}
        <form
          onSubmit={create}
          noValidate
          className="grid gap-3 sm:grid-cols-[1fr_12rem_8rem_auto] sm:items-end"
        >
          <TextField
            label="Token name"
            name="name"
            maxLength={100}
            placeholder="My laptop"
            required
          />
          <SelectField label="For" name="kind" defaultValue="extension">
            <option value="extension">Browser extension</option>
            <option value="desktop">Desktop assistant</option>
          </SelectField>
          <TextField
            label="Valid for (days)"
            name="days"
            type="number"
            inputMode="numeric"
            min={1}
            max={90}
            defaultValue={30}
          />
          <Button type="submit" isLoading={busy}>
            Create token
          </Button>
        </form>
        {tokens.length === 0 ? (
          <p className="text-sm text-muted">No tokens yet.</p>
        ) : (
          <ul className="divide-y divide-line rounded-md border border-line">
            {tokens.map((t) => {
              const expired = new Date(t.expires_at) < new Date();
              return (
                <li
                  key={t.id}
                  className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"
                >
                  <span className="min-w-0">
                    <span className="block font-medium break-words text-ink">{t.name}</span>
                    <span className="block text-xs text-muted">
                      {t.kind === "desktop" ? "Desktop assistant" : "Browser extension"} ·{" "}
                      {t.token_prefix}… · expires {new Date(t.expires_at).toLocaleDateString()}
                      {t.last_used_at
                        ? ` · last used ${new Date(t.last_used_at).toLocaleDateString()}`
                        : " · never used"}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <Badge tone={t.revoked ? "danger" : expired ? "warning" : "success"}>
                      {t.revoked ? "Revoked" : expired ? "Expired" : "Active"}
                    </Badge>
                    {!t.revoked && !expired ? (
                      <Button variant="secondary" size="sm" onClick={() => revoke(t.id)}>
                        Revoke
                      </Button>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
