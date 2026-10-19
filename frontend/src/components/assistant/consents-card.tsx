"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { TextField } from "@/components/ui/field";
import { SelectField } from "@/components/ui/select";
import { TextAreaField } from "@/components/ui/textarea-field";
import { apiRequest } from "@/lib/client-api";
import type { LearningConsent } from "@/lib/types";

const BANDS = ["6-8", "9-11", "12-14", "15-17"];

/**
 * Guardian consent records for the learning helper. The installer collects consent in their own
 * process; KMR AI records it, signs a token for it, and refuses under-18 learners without one.
 */
export function ConsentsCard({
  orgId,
  installId,
  consents,
}: {
  orgId: string;
  installId: string;
  consents: LearningConsent[] | null;
}) {
  const router = useRouter();
  const base = `/organizations/${orgId}/assistant/installs/${installId}/consents`;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);

  async function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError(null);
    const result = await apiRequest<{ token: string }>("POST", base, {
      label: String(data.get("label") ?? "").trim(),
      age_band: String(data.get("age_band")),
      consent_basis: String(data.get("basis") ?? "").trim(),
      days: Number(data.get("days") ?? 180),
    });
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setToken(result.data.token);
    form.reset();
    router.refresh();
  }

  async function revoke(id: string) {
    if (!window.confirm("Revoke this consent? The learner's access stops at once.")) return;
    const result = await apiRequest("POST", `${base}/${id}/revoke`);
    if (!result.ok) return setError(result.error.message);
    router.refresh();
  }

  return (
    <section aria-label="Guardian consent" className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold text-ink">Guardian consent (learners under 18)</h3>
      <p className="text-xs text-muted">
        Learners under 18 can only use the learning helper with a consent token. Collect the
        guardian&apos;s consent in your own process first, then record it here. KMR AI cannot check
        that you did. Use your own reference as the label (a class code or enrolment number), not a
        child&apos;s name.
      </p>
      {error ? (
        <Alert tone="danger" title="That did not work">
          {error}
        </Alert>
      ) : null}
      {token ? (
        <Alert tone="warning" title="Copy this token now. It will not be shown again.">
          <p className="font-mono text-xs break-all">{token}</p>
          <p className="mt-2 text-xs">
            Give it to the learner&apos;s session from your own page with{" "}
            <code>KMRWidget.setConsent(token)</code>, or put it in the script tag as{" "}
            <code>data-consent-token</code>.
          </p>
          <div className="mt-3">
            <CopyButton value={token} label="consent token" />
          </div>
        </Alert>
      ) : null}
      <form onSubmit={issue} noValidate className="grid gap-3 sm:grid-cols-2">
        <TextField label="Your reference (not a name)" name="label" maxLength={60} required />
        <SelectField label="Age group" name="age_band" defaultValue="12-14">
          {BANDS.map((b) => (
            <option key={b} value={b}>
              Age {b}
            </option>
          ))}
        </SelectField>
        <div className="sm:col-span-2">
          <TextAreaField
            label="How you collected the consent"
            name="basis"
            maxLength={300}
            hint="For example: signed paper form on file at the school office."
          />
        </div>
        <TextField
          label="Valid for (days)"
          name="days"
          type="number"
          inputMode="numeric"
          min={1}
          max={366}
          defaultValue={180}
        />
        <div className="flex items-end">
          <Button type="submit" variant="secondary" isLoading={busy}>
            Record consent and create token
          </Button>
        </div>
      </form>
      {consents === null ? (
        <Alert tone="danger" title="Could not load consents">
          Reload the page to try again.
        </Alert>
      ) : consents.length === 0 ? (
        <p className="text-sm text-muted">No consents recorded yet.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {consents.map((c) => {
            const expired = new Date(c.expires_at) < new Date();
            return (
              <li
                key={c.id}
                className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"
              >
                <span className="min-w-0">
                  <span className="block font-medium break-words text-ink">
                    {c.label} · age {c.age_band}
                  </span>
                  <span className="block text-xs break-words text-muted">
                    {c.consent_basis} · expires {new Date(c.expires_at).toLocaleDateString()}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <Badge tone={c.revoked ? "danger" : expired ? "warning" : "success"}>
                    {c.revoked ? "Revoked" : expired ? "Expired" : "Active"}
                  </Badge>
                  {!c.revoked && !expired ? (
                    <Button variant="secondary" size="sm" onClick={() => revoke(c.id)}>
                      Revoke
                    </Button>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
