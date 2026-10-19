"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { cn } from "@/lib/cn";
import { appRoutes, mobileTabHrefs } from "@/lib/routes";

const tabs = appRoutes.filter((route) => mobileTabHrefs.includes(route.href));

/** App-style tab bar for phones. "More" opens the full navigation drawer. */
function Tabs({ pathname, onMore }: { pathname: string; onMore: () => void }) {
  const item =
    "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 px-1 text-xs font-medium";
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] lg:hidden"
    >
      <ul className="flex">
        {tabs.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <li key={href} className="flex flex-1">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(item, active ? "text-primary-text" : "text-muted")}
              >
                <span
                  className={cn(
                    "flex h-7 w-14 items-center justify-center rounded-full transition-colors",
                    active && "bg-primary-soft",
                  )}
                >
                  <Icon className="size-5" aria-hidden="true" />
                </span>
                {label}
              </Link>
            </li>
          );
        })}
        <li className="flex flex-1">
          <button type="button" onClick={onMore} className={cn(item, "text-muted")}>
            <span className="flex h-7 w-14 items-center justify-center">
              <Menu className="size-5" aria-hidden="true" />
            </span>
            More
          </button>
        </li>
      </ul>
    </nav>
  );
}

function CurrentTabs({ onMore }: { onMore: () => void }) {
  return <Tabs pathname={usePathname()} onMore={onMore} />;
}

// usePathname() needs Suspense on dynamic routes; the fallback is the bar without a current tab.
export function BottomNav({ onMore }: { onMore: () => void }) {
  return (
    <Suspense fallback={<Tabs pathname="" onMore={onMore} />}>
      <CurrentTabs onMore={onMore} />
    </Suspense>
  );
}
