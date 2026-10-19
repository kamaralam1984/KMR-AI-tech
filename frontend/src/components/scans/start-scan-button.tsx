"use client";

import { ScanSearch } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { SelectField } from "@/components/ui/select";
import { apiRequest } from "@/lib/client-api";
import { ENGINE_LABEL, SCAN_PROFILES } from "@/lib/scan-profiles";
import type { Scan } from "@/lib/types";

function scanQuery(profile: string, engine: string): string {
  const parts = [
    profile === "desktop" ? "" : `profile=${profile}`,
    engine === "chromium" ? "" : `engine=${engine}`,
  ].filter(Boolean);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** Starts a read-only scan. `blockedReason` explains why it cannot start, in plain words. */
export function StartScanButton({
  orgId,
  projectId,
  blockedReason,
  activeScanId,
  engines = ["chromium"],
}: {
  orgId: string;
  projectId: string;
  blockedReason: string | null;
  activeScanId: string | null;
  engines?: string[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<string>("desktop");
  const [engine, setEngine] = useState<string>("chromium");
  const chosen = SCAN_PROFILES.find((p) => p.key === profile);

  if (activeScanId) {
    return (
      <p className="text-sm text-ink">
        A scan is already in progress.{" "}
        <Link
          href={`/scans/${activeScanId}`}
          className="font-medium text-primary-text underline underline-offset-2"
        >
          View it
        </Link>
      </p>
    );
  }

  async function start() {
    setBusy(true);
    setError(null);
    const result = await apiRequest<Scan>(
      "POST",
      `/organizations/${orgId}/projects/${projectId}/scans${scanQuery(profile, engine)}`,
      undefined,
      // One key per click: a double tap or a retry cannot start two scans.
      { headers: { "Idempotency-Key": crypto.randomUUID() } },
    );
    if (result.ok) {
      router.push(`/scans/${result.data.id}`);
      return;
    }
    setBusy(false);
    setError(result.error.message);
  }

  return (
    <div className="flex flex-col gap-3">
      {error ? (
        <Alert tone="danger" title="Could not start the scan">
          {error}
        </Alert>
      ) : null}
      <SelectField
        label="Scan as"
        value={profile}
        onChange={(event) => setProfile(event.target.value)}
        hint={`${chosen?.note ?? ""} Uses ${chosen?.weight ?? 1} of your daily scans. Load times are measured in the scan environment, not by real visitors.`}
        className="max-w-xs"
      >
        {SCAN_PROFILES.map((p) => (
          <option key={p.key} value={p.key}>
            {p.label}
          </option>
        ))}
      </SelectField>
      {engines.length > 1 ? (
        <SelectField
          label="Browser"
          value={engine}
          onChange={(event) => setEngine(event.target.value)}
          hint="Slow-network and low-end-phone profiles need Chromium."
          className="max-w-xs"
        >
          {engines.map((e) => (
            <option key={e} value={e}>
              {ENGINE_LABEL[e] ?? e}
            </option>
          ))}
        </SelectField>
      ) : null}
      <div>
        <Button onClick={start} isLoading={busy} disabled={blockedReason !== null}>
          <ScanSearch className="size-4" aria-hidden="true" />
          Start scan
        </Button>
      </div>
      {blockedReason ? <p className="text-sm text-muted">{blockedReason}</p> : null}
      <p className="text-xs text-muted">
        A scan only reads your pages. It never submits forms, clicks buttons or changes anything.
      </p>
    </div>
  );
}
