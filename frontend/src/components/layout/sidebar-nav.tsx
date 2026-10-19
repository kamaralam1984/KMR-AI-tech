"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { cn } from "@/lib/cn";
import { appRoutes } from "@/lib/routes";

type NavProps = { onNavigate?: () => void };

function NavList({ pathname, onNavigate }: NavProps & { pathname: string }) {
  return (
    <nav aria-label="Main">
      <ul className="flex flex-col gap-0.5">
        {appRoutes.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <li key={href}>
              <Link
                href={href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-md border-l-2 px-3 py-2 text-sm font-medium transition-colors duration-150",
                  active
                    ? "border-accent bg-navy-700 text-white"
                    : "border-transparent text-white/75 hover:bg-navy-700/60 hover:text-white",
                )}
              >
                <Icon className="size-4.5 shrink-0" aria-hidden="true" />
                <span className="flex-1">{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function CurrentNav({ onNavigate }: NavProps) {
  return <NavList pathname={usePathname()} onNavigate={onNavigate} />;
}

/**
 * usePathname() is only known at request time on dynamic routes such as /projects/[id], so it
 * must sit inside Suspense. The fallback is the same menu without a highlighted item.
 */
export function SidebarNav({ onNavigate }: NavProps) {
  return (
    <Suspense fallback={<NavList pathname="" onNavigate={onNavigate} />}>
      <CurrentNav onNavigate={onNavigate} />
    </Suspense>
  );
}
