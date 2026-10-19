"use client";

import { UserMinus } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { TextField } from "@/components/ui/field";
import { SelectField } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { assignableRoles, canRemove, roleLabel } from "@/lib/org";
import type { Member, Role } from "@/lib/types";

export function MembersPanel({
  orgId,
  myUserId,
  myRole,
  members,
}: {
  orgId: string;
  myUserId: string;
  myRole: Role;
  members: Member[];
}) {
  const router = useRouter();
  const toast = useToast();
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [busy, setBusy] = useState(false);
  const addRoles = assignableRoles(myRole, null);

  async function onAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const email = String(data.get("email") ?? "").trim();
    const role = String(data.get("role") ?? addRoles[addRoles.length - 1]) as Role;
    if (!email.includes("@")) return setAddError("Enter the email address of an existing account.");
    setAddError(null);
    setAdding(true);
    const result = await apiRequest<Member>("POST", `/organizations/${orgId}/members`, {
      email,
      role,
    });
    setAdding(false);
    if (!result.ok) return setAddError(result.error.message);
    form.reset();
    toast({ tone: "success", title: "Member added", description: result.data.email });
    router.refresh();
  }

  async function onChangeRole(member: Member, role: Role) {
    const result = await apiRequest("PATCH", `/organizations/${orgId}/members/${member.user_id}`, {
      role,
    });
    if (result.ok) toast({ tone: "success", title: "Role updated" });
    else
      toast({
        tone: "danger",
        title: "Could not change the role",
        description: result.error.message,
      });
    router.refresh();
  }

  async function onRemove() {
    if (!removing) return;
    setBusy(true);
    const result = await apiRequest(
      "DELETE",
      `/organizations/${orgId}/members/${removing.user_id}`,
    );
    setBusy(false);
    const leaving = removing.user_id === myUserId;
    setRemoving(null);
    if (!result.ok) {
      toast({ tone: "danger", title: "Could not remove", description: result.error.message });
      return;
    }
    toast({ tone: "success", title: leaving ? "You left the organization" : "Member removed" });
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-5">
      {addRoles.length > 0 ? (
        <form onSubmit={onAdd} noValidate className="flex flex-col gap-3 sm:flex-row sm:items-end">
          {addError ? (
            <div className="sm:basis-full">
              <Alert tone="danger" title="Could not add the member">
                {addError}
              </Alert>
            </div>
          ) : null}
          <div className="sm:flex-1">
            <TextField
              label="Add by email"
              type="email"
              name="email"
              inputMode="email"
              autoCapitalize="none"
              required
              hint="They need a KMR AI account first."
            />
          </div>
          <SelectField label="Role" name="role" defaultValue="member">
            {addRoles.map((role) => (
              <option key={role} value={role}>
                {roleLabel(role)}
              </option>
            ))}
          </SelectField>
          <Button type="submit" isLoading={adding}>
            Add member
          </Button>
        </form>
      ) : null}

      <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
        {members.map((member) => {
          const isSelf = member.user_id === myUserId;
          const roles = isSelf ? [] : assignableRoles(myRole, member.role);
          return (
            <li
              key={member.user_id}
              className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-ink">
                  {member.display_name}
                  {isSelf ? (
                    <span className="ml-2 text-sm font-normal text-muted">(you)</span>
                  ) : null}
                </p>
                <p className="truncate text-sm text-muted">{member.email}</p>
              </div>
              <div className="flex items-center gap-2">
                {roles.length > 0 ? (
                  <SelectField
                    label={`Role for ${member.email}`}
                    hideLabel
                    value={member.role}
                    onChange={(event) => onChangeRole(member, event.target.value as Role)}
                  >
                    {Array.from(new Set([member.role, ...roles])).map((role) => (
                      <option key={role} value={role} disabled={!roles.includes(role)}>
                        {roleLabel(role)}
                      </option>
                    ))}
                  </SelectField>
                ) : (
                  <Badge tone={member.role === "owner" ? "info" : "neutral"}>
                    {roleLabel(member.role)}
                  </Badge>
                )}
                {canRemove(myRole, member.role, isSelf) ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setRemoving(member)}
                    aria-label={isSelf ? "Leave organization" : `Remove ${member.email}`}
                  >
                    <UserMinus className="size-4" aria-hidden="true" />
                    <span className="hidden sm:inline">{isSelf ? "Leave" : "Remove"}</span>
                  </Button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      <Dialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={removing?.user_id === myUserId ? "Leave this organization?" : "Remove this member?"}
        description={
          removing?.user_id === myUserId
            ? "You will lose access to its projects. An owner can add you back."
            : `${removing?.email} will lose access to this organization immediately.`
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={onRemove} isLoading={busy}>
              {removing?.user_id === myUserId ? "Leave" : "Remove"}
            </Button>
          </>
        }
      />
    </div>
  );
}
