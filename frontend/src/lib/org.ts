import type { OrgSummary, Role } from "./types";

export const ROLES: Role[] = ["owner", "admin", "member", "viewer"];
const RANK: Record<Role, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };

export const ORG_COOKIE = "kmr_org";

/** The preferred organization if the user still belongs to it, otherwise their first one. */
export function pickActiveOrg(orgs: OrgSummary[], preferredId?: string | null): OrgSummary | null {
  return orgs.find((org) => org.id === preferredId) ?? orgs[0] ?? null;
}

export function atLeast(role: Role, minimum: Role): boolean {
  return RANK[role] >= RANK[minimum];
}

/**
 * Roles the actor may assign to a member whose current role is `target` (null when adding).
 * Mirrors the backend rule so the UI offers only what the API will accept. The API is the
 * authority: this only hides choices that would be refused.
 */
export function assignableRoles(actor: Role, target: Role | null): Role[] {
  if (actor === "owner") return ROLES;
  if (actor !== "admin") return [];
  if (target !== null && RANK[target] >= RANK.admin) return [];
  return ["member", "viewer"];
}

export function canRemove(actor: Role, target: Role, isSelf: boolean): boolean {
  if (isSelf) return true; // anyone may leave; the API protects the last owner
  return assignableRoles(actor, target).length > 0;
}

/** Only same-site relative paths, so a login link cannot redirect to another site. */
export function safeNext(next: string | null | undefined, fallback = "/dashboard"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return fallback;
  }
  return next;
}

export function roleLabel(role: Role): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}
