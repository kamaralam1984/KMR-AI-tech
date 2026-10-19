"use client";

import { Wrench } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { atLeast } from "@/lib/org";
import type { Repair, Repository, Role } from "@/lib/types";

import { RepairActions } from "./repair-actions";
import { RepairCard } from "./repair-card";

/** Plans a fix for one issue. Planning only reads the repository; it never changes it. */
export function RepairPanel({
  orgId,
  projectId,
  issueId,
  role,
  issueOpen,
}: {
  orgId: string;
  projectId: string;
  issueId: string;
  role: Role;
  issueOpen: boolean;
}) {
  const toast = useToast();
  const [repo, setRepo] = useState<Repository | null | undefined>(undefined);
  const [repairs, setRepairs] = useState<Repair[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiRequest<Repository | null>(
        "GET",
        `/organizations/${orgId}/projects/${projectId}/repository`,
      ),
      apiRequest<Repair[]>("GET", `/organizations/${orgId}/issues/${issueId}/repairs`),
    ]).then(([r, list]) => {
      if (cancelled) return;
      setRepo(r.ok ? r.data : null);
      if (list.ok) setRepairs(list.data);
    });
    return () => {
      cancelled = true;
    };
  }, [orgId, projectId, issueId]);

  async function plan() {
    setBusy(true);
    setError(null);
    const result = await apiRequest<Repair>(
      "POST",
      `/organizations/${orgId}/issues/${issueId}/repairs`,
      {},
    );
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setRepairs((current) => [
      result.data,
      ...(current ?? []).map((r) =>
        result.data.status === "proposed" && r.status === "proposed"
          ? { ...r, status: "superseded" as const }
          : r,
      ),
    ]);
    toast({ tone: "success", title: "Repair plan created. Nothing was changed in your code." });
  }

  const canPlan = atLeast(role, "member") && issueOpen && !!repo;
  return (
    <Card
      title="Repair"
      description="A proposed change to your code, shown as an exact diff. Nothing is applied from here."
    >
      <div className="flex flex-col gap-4">
        {repo === null ? (
          <Alert tone="info" title="No repository connected">
            Connect this project&apos;s repository to plan a fix.
            <Link
              href={`/projects/${projectId}`}
              className="mt-1 flex min-h-10 items-center font-medium underline underline-offset-2"
            >
              Open the project page
            </Link>
          </Alert>
        ) : null}
        {error ? (
          <Alert tone="danger" title="Could not plan a fix">
            {error}
          </Alert>
        ) : null}
        {canPlan ? (
          <div>
            <Button onClick={plan} isLoading={busy}>
              <Wrench className="size-4" aria-hidden="true" />
              Plan a fix
            </Button>
          </div>
        ) : null}
        {!issueOpen ? (
          <p className="text-sm text-muted">Only an open issue can be planned for repair.</p>
        ) : null}
        {repairs === null ? null : repairs.length === 0 ? (
          <p className="text-sm text-muted">No repair plans for this issue yet.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {repairs.map((repair) => (
              <div key={repair.id} className="flex flex-col gap-3">
                <RepairCard repair={repair} />
                {repair.status === "proposed" ? (
                  <RepairActions orgId={orgId} repair={repair} role={role} />
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
