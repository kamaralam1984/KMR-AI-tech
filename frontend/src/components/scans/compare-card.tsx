"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { SelectField } from "@/components/ui/select";
import { apiRequest } from "@/lib/client-api";
import { profileLabel } from "@/lib/scan-profiles";
import type { CompareFinding, Scan, ScanCompare } from "@/lib/types";

function Findings({ title, items }: { title: string; items: CompareFinding[] }) {
  return (
    <section aria-label={title} className="flex flex-col gap-1">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {items.length === 0 ? (
        <p className="text-sm text-muted">None.</p>
      ) : (
        <ul className="text-sm text-ink">
          {items.map((f, i) => (
            <li key={i} className="break-words">
              {f.kind.replace(/_/g, " ")} on {f.page} ({f.severity})
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Compares this scan with another completed scan of the same project (for example another profile). */
export function CompareCard({
  orgId,
  scan,
}: {
  orgId: string;
  scan: { id: string; project_id: string; limits: Record<string, unknown> };
}) {
  const [others, setOthers] = useState<Scan[] | null>(null);
  const [result, setResult] = useState<ScanCompare | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    apiRequest<Scan[]>("GET", `/organizations/${orgId}/scans?limit=100`).then((r) => {
      if (!live) return;
      if (r.ok) {
        setOthers(
          r.data.filter(
            (s) => s.project_id === scan.project_id && s.status === "completed" && s.id !== scan.id,
          ),
        );
      } else {
        setOthers([]);
      }
    });
    return () => {
      live = false;
    };
  }, [orgId, scan.id, scan.project_id]);

  async function choose(otherId: string) {
    setResult(null);
    setError(null);
    if (!otherId) return;
    const r = await apiRequest<ScanCompare>(
      "GET",
      `/organizations/${orgId}/scans/${scan.id}/compare/${otherId}`,
    );
    if (r.ok) setResult(r.data);
    else setError(r.error.message);
  }

  if (others === null || others.length === 0) return null;
  return (
    <Card
      title="Compare with another scan"
      description={`This scan ran as: ${profileLabel(scan.limits)}.`}
    >
      <div className="flex flex-col gap-4">
        <SelectField label="Compare with" defaultValue="" onChange={(e) => choose(e.target.value)}>
          <option value="">Choose a scan</option>
          {others.map((s) => (
            <option key={s.id} value={s.id}>
              {new Date(s.created_at).toLocaleString()} · {profileLabel(s.limits)}
            </option>
          ))}
        </SelectField>
        {error ? (
          <Alert tone="danger" title="Could not compare">
            {error}
          </Alert>
        ) : null}
        {result ? (
          <div className="flex flex-col gap-4" aria-live="polite">
            <p className="text-xs text-muted">{result.basis}</p>
            <Findings
              title={`Only in this scan (${result.first.profile.label})`}
              items={result.only_in_first}
            />
            <Findings
              title={`Only in the other scan (${result.second.profile.label})`}
              items={result.only_in_second}
            />
            <p className="text-sm text-ink">Found in both: {result.in_both}</p>
            {result.load_ms.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="pb-2 text-left text-sm font-semibold text-ink">
                    Load time measured by the scanner (ms)
                  </caption>
                  <thead>
                    <tr className="text-muted">
                      <th className="py-1 pr-4 font-medium">Page</th>
                      <th className="py-1 pr-4 font-medium">This scan</th>
                      <th className="py-1 font-medium">Other scan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.load_ms.map((row) => (
                      <tr key={row.page} className="border-t border-line">
                        <td className="py-1 pr-4 break-all">{row.page}</td>
                        <td className="py-1 pr-4">{row.first ?? "not measured"}</td>
                        <td className="py-1">{row.second ?? "not measured"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
