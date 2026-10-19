"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TextField } from "@/components/ui/field";
import { TextAreaField } from "@/components/ui/textarea-field";
import { apiRequest } from "@/lib/client-api";
import type { AssistantInstall } from "@/lib/types";

import { KeyReveal } from "./key-reveal";

const MODULES = [
  { value: "guide", label: "Website guide", hint: "Answers by quoting your indexed pages." },
  { value: "forms", label: "Form helper", hint: "Explains and checks form fields. Never submits." },
  {
    value: "learning",
    label: "Learning support",
    hint: "Typed questions only. Needs the child-safeguards acknowledgement below.",
  },
];

export function CreateInstallCard({ orgId }: { orgId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<(AssistantInstall & { install_key: string }) | null>(null);
  const [learning, setLearning] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const origins = String(data.get("origins") ?? "")
      .split(/[\s,]+/)
      .filter(Boolean);
    setBusy(true);
    setError(null);
    const result = await apiRequest<AssistantInstall & { install_key: string }>(
      "POST",
      `/organizations/${orgId}/assistant/installs`,
      {
        name: String(data.get("name") ?? "").trim(),
        allowed_origins: origins,
        modules: data.getAll("modules").map(String),
        consent_text: String(data.get("consent_text") ?? "").trim(),
        acknowledge_child_safeguards: data.get("ack") === "on",
      },
    );
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setCreated(result.data);
    form.reset();
    setLearning(false);
    router.refresh();
  }

  return (
    <Card
      title="New assistant install"
      description="The install key is shown once. Only its hash is stored."
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        {error ? (
          <Alert tone="danger" title="Could not create the install">
            {error}
          </Alert>
        ) : null}
        <TextField label="Name" name="name" maxLength={100} required />
        <TextAreaField
          label="Allowed website addresses"
          name="origins"
          placeholder="https://example.com"
          hint="One per line. Exact origins only: no paths, no wildcards. http://localhost works for development."
          spellCheck={false}
        />
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium text-ink">Helpers</legend>
          {MODULES.map((m) => (
            <label key={m.value} className="flex items-start gap-2 text-sm text-ink">
              <input
                type="checkbox"
                name="modules"
                value={m.value}
                defaultChecked={m.value === "guide"}
                onChange={m.value === "learning" ? (e) => setLearning(e.target.checked) : undefined}
                className="mt-1 size-4"
              />
              <span>
                {m.label}
                <span className="block text-xs text-muted">{m.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {learning ? (
          <label className="flex items-start gap-2 text-sm text-ink">
            <input type="checkbox" name="ack" className="mt-1 size-4" />
            <span>
              I have read the child-data safeguards (docs/CHILD-LEARNING-SAFEGUARDS.md) and
              understand that a legal review is still required before real learners use this.
            </span>
          </label>
        ) : null}
        <TextAreaField
          label="Consent text for the support hand-off (optional)"
          name="consent_text"
          maxLength={1000}
          hint="Shown to a visitor before they choose to send a summary to your support team."
        />
        <div>
          <Button type="submit" isLoading={busy}>
            Create install
          </Button>
        </div>
      </form>
      {created ? <KeyReveal install={created} /> : null}
    </Card>
  );
}
