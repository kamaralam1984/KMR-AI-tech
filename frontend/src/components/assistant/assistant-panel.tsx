"use client";

import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TextField } from "@/components/ui/field";
import { TextAreaField } from "@/components/ui/textarea-field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import type {
  AssistantHandoff,
  AssistantInstall,
  AssistantPage,
  LearningConsent,
} from "@/lib/types";

import { ConsentsCard } from "./consents-card";
import { KeyReveal } from "./key-reveal";

type IndexResult = { path: string; status: string; detail: string | null };

export function AssistantPanel({
  orgId,
  install,
  pages,
  handoffs,
  consents,
}: {
  orgId: string;
  install: AssistantInstall;
  pages: AssistantPage[] | null;
  handoffs: AssistantHandoff[] | null;
  consents?: LearningConsent[] | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const base = `/organizations/${orgId}/assistant/installs/${install.id}`;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rotated, setRotated] = useState<(AssistantInstall & { install_key: string }) | null>(null);
  const [results, setResults] = useState<IndexResult[] | null>(null);

  async function run<T>(name: string, fn: () => ReturnType<typeof apiRequest<T>>) {
    setBusy(name);
    setError(null);
    const result = await fn();
    setBusy(null);
    if (!result.ok) {
      setError(result.error.message);
      return null;
    }
    router.refresh();
    return result.data;
  }

  async function rotate() {
    const data = await run("rotate", () =>
      apiRequest<AssistantInstall & { install_key: string }>("POST", `${base}/rotate-key`),
    );
    if (data) setRotated(data);
  }

  async function revoke() {
    if (!window.confirm("Revoke this install? The widget on your site stops working at once."))
      return;
    const data = await run("revoke", () => apiRequest("POST", `${base}/revoke`));
    if (data !== null) toast({ tone: "success", title: "Install revoked" });
  }

  async function addPage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const d = new FormData(form);
    const url = String(d.get("url") ?? "").trim();
    const data = await run("page", () =>
      apiRequest("POST", `${base}/pages`, {
        kind: "site",
        title: String(d.get("title") ?? "").trim(),
        url: url || null,
        content: String(d.get("content") ?? ""),
      }),
    );
    if (data !== null) form.reset();
  }

  async function indexPaths(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const paths = String(new FormData(event.currentTarget).get("paths") ?? "")
      .split(/[\s,]+/)
      .filter(Boolean);
    if (paths.length === 0) return setError("Enter at least one path, such as /about.");
    const data = await run("index", () =>
      apiRequest<IndexResult[]>("POST", `${base}/index`, { paths }),
    );
    if (data) setResults(data);
  }

  async function remove(kind: "pages" | "handoffs", id: string) {
    if (!window.confirm("Delete this item? This cannot be undone.")) return;
    await run(`del-${id}`, () => apiRequest("DELETE", `${base}/${kind}/${id}`));
  }

  async function resolve(id: string) {
    await run(`res-${id}`, () =>
      apiRequest("PATCH", `${base}/handoffs/${id}`, { status: "resolved" }),
    );
  }

  const inactive = install.revoked || !install.enabled;

  return (
    <Card title={install.name} description={`Key starts with ${install.key_prefix}`}>
      <div className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={install.revoked ? "danger" : inactive ? "warning" : "success"}>
            {install.revoked ? "Revoked" : inactive ? "Disabled" : "Active"}
          </Badge>
          {install.modules.map((m) => (
            <Badge key={m} tone="info">
              {m}
            </Badge>
          ))}
          {install.modules.includes("learning") ? (
            <Badge tone="warning">Learning: not production-ready</Badge>
          ) : null}
        </div>
        <p className="text-sm text-muted break-words">
          Allowed addresses:{" "}
          {install.allowed_origins.length ? install.allowed_origins.join(", ") : "none yet"}
        </p>
        {error ? (
          <Alert tone="danger" title="That did not work">
            {error}
          </Alert>
        ) : null}
        {rotated ? <KeyReveal install={rotated} /> : null}
        {!install.revoked ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" isLoading={busy === "rotate"} onClick={rotate}>
              Rotate key
            </Button>
            <Button variant="danger" size="sm" isLoading={busy === "revoke"} onClick={revoke}>
              Revoke
            </Button>
          </div>
        ) : null}

        <section aria-labelledby={`pages-${install.id}`} className="flex flex-col gap-3">
          <h3 id={`pages-${install.id}`} className="text-sm font-semibold text-ink">
            Knowledge pages
          </h3>
          <p className="text-xs text-muted">
            Only these pages are used to answer. Pages that contain instruction-like text are
            flagged and those passages are never returned.
          </p>
          {pages === null ? (
            <Alert tone="danger" title="Could not load pages">
              Reload the page to try again.
            </Alert>
          ) : pages.length === 0 ? (
            <p className="text-sm text-muted">No pages yet. Add text below or index your site.</p>
          ) : (
            <ul className="divide-y divide-line rounded-md border border-line">
              {pages.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 p-3 text-sm">
                  <span className="min-w-0">
                    <span className="block font-medium break-words text-ink">{p.title}</span>
                    <span className="block text-xs break-all text-muted">
                      {p.url ?? "no URL"} · {p.source} · {p.chunk_count} passages
                      {p.blocked_chunks ? ` · ${p.blocked_chunks} blocked` : ""}
                    </span>
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Delete page ${p.title}`}
                    onClick={() => remove("pages", p.id)}
                  >
                    <Trash2 className="size-4" aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={indexPaths} noValidate className="flex flex-col gap-3">
            <TextField
              label="Index public pages from your verified website"
              name="paths"
              placeholder="/about /contact"
              hint="Paths only. Fetched read-only through the safe fetcher, from your verified host."
              autoCapitalize="none"
            />
            <div>
              <Button type="submit" variant="secondary" size="sm" isLoading={busy === "index"}>
                Index pages
              </Button>
            </div>
          </form>
          {results ? (
            <ul className="text-xs text-muted" aria-live="polite">
              {results.map((r) => (
                <li key={r.path}>
                  {r.path}: {r.status}
                  {r.detail ? ` (${r.detail})` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          <form onSubmit={addPage} noValidate className="flex flex-col gap-3">
            <TextField label="Page title" name="title" maxLength={200} required />
            <TextField label="Page URL (optional)" name="url" maxLength={500} />
            <TextAreaField
              label="Page text"
              name="content"
              hint="At least 20 characters. Paste only public content."
            />
            <div>
              <Button type="submit" variant="secondary" size="sm" isLoading={busy === "page"}>
                Add page
              </Button>
            </div>
          </form>
        </section>

        {consents !== undefined ? (
          <ConsentsCard orgId={orgId} installId={install.id} consents={consents} />
        ) : null}

        <section aria-labelledby={`handoffs-${install.id}`} className="flex flex-col gap-3">
          <h3 id={`handoffs-${install.id}`} className="text-sm font-semibold text-ink">
            Support hand-offs
          </h3>
          {handoffs === null ? (
            <Alert tone="danger" title="Could not load hand-offs">
              Reload the page to try again.
            </Alert>
          ) : handoffs.length === 0 ? (
            <p className="text-sm text-muted">
              None. A hand-off appears only after a visitor previews and consents to sending it.
              They are deleted after {install.handoff_retention_days} days.
            </p>
          ) : (
            <ul className="divide-y divide-line rounded-md border border-line">
              {handoffs.map((h) => (
                <li key={h.id} className="flex flex-col gap-2 p-3 text-sm">
                  <p className="break-words whitespace-pre-wrap text-ink">{h.summary}</p>
                  <p className="text-xs text-muted">
                    {h.contact ? `Contact: ${h.contact} · ` : ""}
                    Expires {new Date(h.expires_at).toLocaleDateString()} ·{" "}
                    <Badge tone={h.status === "resolved" ? "success" : "warning"}>
                      {h.status === "resolved" ? "Resolved" : "New"}
                    </Badge>
                  </p>
                  <div className="flex gap-2">
                    {h.status !== "resolved" ? (
                      <Button variant="secondary" size="sm" onClick={() => resolve(h.id)}>
                        Mark resolved
                      </Button>
                    ) : null}
                    <Button variant="ghost" size="sm" onClick={() => remove("handoffs", h.id)}>
                      Delete
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Card>
  );
}
