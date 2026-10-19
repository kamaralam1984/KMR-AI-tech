"use client";

import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";

import { AccountMenu } from "./account-menu";
import { BottomNav } from "./bottom-nav";
import { Brand } from "./brand";
import { SidebarNav } from "./sidebar-nav";

function SidebarPanel({ onNavigate, onClose }: { onNavigate?: () => void; onClose?: () => void }) {
  return (
    <div className="on-dark flex h-full flex-col gap-6 bg-navy-950 p-4 pt-[max(1rem,env(safe-area-inset-top))] text-white">
      <div className="flex items-center justify-between px-1">
        <Brand />
        {onClose ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Close navigation"
            className="text-white hover:bg-navy-700"
          >
            <X className="size-4" aria-hidden="true" />
          </Button>
        ) : null}
      </div>
      <SidebarNav onNavigate={onNavigate} />
      <p className="mt-auto px-1 pb-[env(safe-area-inset-bottom)] text-xs text-white/60">
        Pre-release build for a controlled beta. Not for production use.
      </p>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLDialogElement>(null);

  // The "More" drawer is a native <dialog>: focus trap and Escape come from the browser.
  useEffect(() => {
    const dialog = drawerRef.current;
    if (!dialog) return;
    if (drawerOpen && !dialog.open) dialog.showModal();
    if (!drawerOpen && dialog.open) dialog.close();
  }, [drawerOpen]);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-dvh lg:block">
        <SidebarPanel />
      </aside>

      <dialog
        ref={drawerRef}
        aria-label="Navigation"
        onClose={() => setDrawerOpen(false)}
        onClick={(event) => {
          if (event.target === drawerRef.current) setDrawerOpen(false);
        }}
        className="m-0 h-dvh max-h-none w-[min(18rem,85vw)] border-0 bg-transparent p-0 text-white backdrop:bg-navy-950/60 lg:hidden"
      >
        <SidebarPanel
          onNavigate={() => setDrawerOpen(false)}
          onClose={() => setDrawerOpen(false)}
        />
      </dialog>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-20 flex min-h-14 items-center justify-between gap-3 border-b border-line bg-surface px-4 pt-[env(safe-area-inset-top)] sm:px-6">
          <div className="lg:hidden">
            <Brand />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Tooltip content="Features marked as not available are planned. Nothing on those pages is live data.">
              <button type="button" className="hidden rounded-full sm:inline-flex">
                <Badge tone="info">Pre-release</Badge>
              </button>
            </Tooltip>
            <AccountMenu />
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-6xl flex-1 px-4 pt-6 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:px-6 lg:py-8"
        >
          {children}
        </main>
      </div>

      <BottomNav onMore={() => setDrawerOpen(true)} />
    </div>
  );
}
