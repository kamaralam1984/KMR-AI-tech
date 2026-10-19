"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { TextField } from "@/components/ui/field";
import { TextAreaField } from "@/components/ui/textarea-field";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { formatWhen } from "@/lib/scan-text";
import type { Project, VerifyResult } from "@/lib/types";

function Fact({ label, value, copyLabel }: { label: string; value: string; copyLabel: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="flex items-center gap-2">
        <code className="min-w-0 flex-1 rounded-md bg-page px-2 py-1.5 text-xs break-all text-ink">
          {value}
        </code>
        <CopyButton value={value} label={copyLabel} />
      </dd>
    </div>
  );
}

/** The website to scan, and the proof that you own it. Scanning stays off until it is proven. */
export function SiteCard({
  orgId,
  project,
  canEdit,
}: {
  orgId: string;
  project: Project;
  canEdit: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const { site } = project;
  const base = `/organizations/${orgId}/projects/${project.id}`;
  const [urlError, setUrlError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | undefined>();
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState<"dns" | "file" | null>(null);
  const [notFound, setNotFound] = useState<{ method: string; reason: string } | null>(null);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const siteUrl = String(data.get("site_url") ?? "").trim();
    const exclusions = String(data.get("exclusions") ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const maxPages = Number(data.get("max_pages") ?? 10);
    const injectRequestId = data.get("inject_request_id") === "on";
    if (!siteUrl) return setUrlError("Enter your website address.");
    setUrlError(undefined);
    setFormError(undefined);
    setSaving(true);
    const result = await apiRequest<Project>("PUT", `${base}/site`, {
      site_url: siteUrl,
      exclusions,
      max_pages: maxPages,
      inject_request_id: injectRequestId,
    });
    setSaving(false);
    if (!result.ok) {
      if (result.error.status === 422 && !result.error.fieldErrors.exclusions) {
        return setUrlError(result.error.message);
      }
      return setFormError(result.error.fieldErrors.exclusions ?? result.error.message);
    }
    toast({ tone: "success", title: "Website saved" });
    setNotFound(null);
    router.refresh();
  }

  async function verify(method: "dns" | "file") {
    setChecking(method);
    setNotFound(null);
    const result = await apiRequest<VerifyResult>("POST", `${base}/verify`, { method });
    setChecking(null);
    if (!result.ok) {
      toast({ tone: "danger", title: "Could not check", description: result.error.message });
      return;
    }
    if (result.data.verified) {
      toast({ tone: "success", title: "Ownership verified" });
      router.refresh();
    } else {
      setNotFound({ method, reason: result.data.reason ?? "Not found yet." });
    }
  }

  const v = site.verification;
  return (
    <div className="flex flex-col gap-6">
      {canEdit ? (
        <form onSubmit={onSave} noValidate className="flex flex-col gap-4">
          {formError ? (
            <Alert tone="danger" title="Could not save">
              {formError}
            </Alert>
          ) : null}
          <TextField
            key={site.site_url ?? "new"}
            label="Website address"
            name="site_url"
            type="url"
            inputMode="url"
            autoCapitalize="none"
            defaultValue={site.site_url ?? ""}
            placeholder="https://www.example.com/"
            hint="The page the scan starts from. Only this host is scanned."
            required
            error={urlError}
          />
          <TextAreaField
            key={`ex-${site.exclusions.join(",")}`}
            label="Pages to skip (one path per line)"
            name="exclusions"
            defaultValue={site.exclusions.join("\n")}
            placeholder={"/admin\n/account"}
            hint="Anything under these paths is not visited."
          />
          <TextField
            key={`mp-${site.max_pages}`}
            label="Most pages to scan"
            name="max_pages"
            type="number"
            inputMode="numeric"
            min={1}
            max={50}
            defaultValue={site.max_pages}
          />
          <label className="flex items-start gap-3 text-sm text-ink">
            <input
              type="checkbox"
              name="inject_request_id"
              defaultChecked={site.inject_request_id}
              key={`rid-${site.inject_request_id}`}
              className="mt-0.5 size-5 shrink-0 accent-primary"
            />
            <span>
              Add an <code>X-Request-ID</code> header to this site&apos;s own requests during scans.
              It lets you match your backend logs to what the browser saw. It is never sent to other
              websites.
            </span>
          </label>
          <Button type="submit" isLoading={saving} className="self-start">
            Save website
          </Button>
        </form>
      ) : (
        <p className="text-sm text-muted">
          {site.site_url ? `Website: ${site.site_url}` : "No website has been set yet."} Members can
          change this.
        </p>
      )}

      {site.site_url && site.site_host ? (
        <section
          aria-labelledby="ownership-heading"
          className="flex flex-col gap-4 border-t border-line pt-5"
        >
          <div className="flex flex-wrap items-center gap-2">
            <h3 id="ownership-heading" className="text-sm font-semibold text-ink">
              Ownership of {site.site_host}
            </h3>
            {v.verified ? (
              <Badge tone="success">Verified by {v.method === "dns" ? "DNS" : "file"}</Badge>
            ) : (
              <Badge tone="warning">Not verified yet</Badge>
            )}
          </div>

          {v.verified ? (
            <p className="text-sm text-muted">
              Verified on {formatWhen(v.verified_at)}. If you change the website to a different
              host, you will need to verify again.
            </p>
          ) : (
            <>
              <p className="text-sm text-ink">
                We only scan sites you control. Prove it with <strong>either</strong> option, then
                press its check button.
              </p>
              {notFound ? (
                <Alert
                  tone="warning"
                  title={`${notFound.method === "dns" ? "DNS record" : "File"} not confirmed`}
                >
                  {notFound.reason}
                </Alert>
              ) : null}
              <div className="grid gap-5 lg:grid-cols-2">
                <div className="flex flex-col gap-3 rounded-lg border border-line p-4">
                  <h4 className="text-sm font-semibold text-ink">Option 1: DNS record</h4>
                  <dl className="flex flex-col gap-3">
                    <Fact
                      label="Name"
                      value={v.dns_record_name ?? ""}
                      copyLabel="the DNS record name"
                    />
                    <Fact label="Type" value="TXT" copyLabel="the record type" />
                    <Fact
                      label="Value"
                      value={v.dns_record_value ?? ""}
                      copyLabel="the DNS record value"
                    />
                  </dl>
                  <p className="text-xs text-muted">
                    DNS changes can take a few minutes to appear.
                  </p>
                  {canEdit ? (
                    <Button
                      variant="secondary"
                      onClick={() => verify("dns")}
                      isLoading={checking === "dns"}
                      disabled={checking !== null}
                    >
                      Check DNS record
                    </Button>
                  ) : null}
                </div>
                <div className="flex flex-col gap-3 rounded-lg border border-line p-4">
                  <h4 className="text-sm font-semibold text-ink">Option 2: a file on your site</h4>
                  <dl className="flex flex-col gap-3">
                    <Fact
                      label="Publish this file at"
                      value={v.file_url ?? ""}
                      copyLabel="the file address"
                    />
                    <Fact
                      label="With exactly this content"
                      value={v.file_content ?? ""}
                      copyLabel="the file content"
                    />
                  </dl>
                  <p className="text-xs text-muted">
                    The address must answer directly, without redirects.
                  </p>
                  {canEdit ? (
                    <Button
                      variant="secondary"
                      onClick={() => verify("file")}
                      isLoading={checking === "file"}
                      disabled={checking !== null}
                    >
                      Check file
                    </Button>
                  ) : null}
                </div>
              </div>
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}
