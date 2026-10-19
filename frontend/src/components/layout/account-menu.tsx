"use client";

import { LogOut, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { SelectField } from "@/components/ui/select";
import { apiRequest } from "@/lib/client-api";
import { ORG_COOKIE, pickActiveOrg, roleLabel } from "@/lib/org";
import type { Me } from "@/lib/types";

function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((part) => part.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=")[1] ?? "") : null;
}

function rememberOrg(id: string) {
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie = `${ORG_COOKIE}=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax${secure}`;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; me: Me }
  | { kind: "error"; message: string };

/** Account sheet: who you are, the active organization, and sign out. Loads when opened. */
export function AccountMenu() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [busy, setBusy] = useState(false);

  async function openMenu() {
    setOpen(true);
    setState({ kind: "loading" });
    const result = await apiRequest<Me>("GET", "/auth/me");
    if (result.ok) setState({ kind: "ready", me: result.data });
    else if (result.error.status === 401) router.replace("/login");
    else setState({ kind: "error", message: result.error.message });
  }

  async function signOut(everywhere: boolean) {
    setBusy(true);
    await apiRequest("POST", everywhere ? "/auth/logout-all" : "/auth/logout");
    setBusy(false);
    setOpen(false);
    router.replace("/login");
    router.refresh();
  }

  const me = state.kind === "ready" ? state.me : null;
  const active = me ? pickActiveOrg(me.organizations, readCookie(ORG_COOKIE)) : null;

  return (
    <>
      <Button variant="ghost" size="sm" onClick={openMenu} aria-label="Account">
        <UserRound className="size-5" aria-hidden="true" />
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Account"
        description={me ? `${me.user.display_name} · ${me.user.email}` : undefined}
      >
        {state.kind === "loading" ? <p className="text-muted">Loading…</p> : null}
        {state.kind === "error" ? (
          <p role="alert" className="text-danger-text">
            {state.message}
          </p>
        ) : null}
        {me ? (
          <div className="flex flex-col gap-4">
            {me.organizations.length > 0 ? (
              <SelectField
                label="Organization"
                value={active?.id ?? ""}
                onChange={(event) => {
                  rememberOrg(event.target.value);
                  setOpen(false);
                  router.refresh();
                }}
                hint={active ? `Your role: ${roleLabel(active.role)}` : undefined}
              >
                {me.organizations.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </SelectField>
            ) : (
              <p className="text-muted">You are not in an organization yet.</p>
            )}
            <div className="flex flex-col gap-2">
              <Button variant="secondary" onClick={() => signOut(false)} isLoading={busy}>
                <LogOut className="size-4" aria-hidden="true" />
                Sign out
              </Button>
              <Button variant="ghost" onClick={() => signOut(true)} disabled={busy}>
                Sign out of all devices
              </Button>
            </div>
          </div>
        ) : null}
      </Dialog>
    </>
  );
}
