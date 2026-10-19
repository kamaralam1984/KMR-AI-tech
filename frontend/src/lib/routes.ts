import {
  CheckSquare,
  FolderKanban,
  MessageSquareText,
  History,
  LayoutDashboard,
  type LucideIcon,
  ScanSearch,
  Settings,
  TriangleAlert,
  Wrench,
} from "lucide-react";

export type AppRoute = {
  href: string;
  label: string;
  icon: LucideIcon;
  summary: string;
};

export const appRoutes: AppRoute[] = [
  {
    href: "/dashboard",
    label: "Dashboard",
    icon: LayoutDashboard,
    summary: "Overview and system status.",
  },
  {
    href: "/projects",
    label: "Projects",
    icon: FolderKanban,
    summary: "Your organization's projects, their website, repository and API checks.",
  },
  {
    href: "/scans",
    label: "Scans",
    icon: ScanSearch,
    summary: "Read-only scans of your verified websites: progress, results and history.",
  },
  {
    href: "/issues",
    label: "Issues",
    icon: TriangleAlert,
    summary: "Findings with evidence, severity, and a clear split between facts and hypotheses.",
  },
  {
    href: "/repairs",
    label: "Repairs",
    icon: Wrench,
    summary: "Proposed fixes on isolated branches, with the exact diff and test results.",
  },
  {
    href: "/approvals",
    label: "Approvals",
    icon: CheckSquare,
    summary: "Approve or reject a specific change. Nothing is applied without you.",
  },
  {
    href: "/history",
    label: "History & audit",
    icon: History,
    summary: "A record of scans, decisions, and results.",
  },
  {
    href: "/assistant",
    label: "Assistant",
    icon: MessageSquareText,
    summary: "A visitor helper you install on your own website. It quotes your pages; no AI model.",
  },
  {
    href: "/settings",
    label: "Settings",
    icon: Settings,
    summary: "Organization, members, roles, and your account.",
  },
];

export function findRoute(href: string): AppRoute | undefined {
  return appRoutes.find((route) => route.href === href);
}

/** The tabs shown in the mobile bottom bar. Everything else is under "More". */
export const mobileTabHrefs = ["/dashboard", "/projects", "/scans", "/settings"];
