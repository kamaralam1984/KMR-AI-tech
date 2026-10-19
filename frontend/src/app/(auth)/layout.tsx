import { Brand } from "@/components/layout/brand";
import { GoldLines } from "@/components/marketing/gold-lines";
import { Sparkles } from "@/components/marketing/sparkles";

export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="relative isolate flex min-h-dvh flex-col items-center justify-center gap-6 overflow-hidden bg-navy-950 px-4 py-10 pt-[max(2.5rem,env(safe-area-inset-top))] pb-[max(2.5rem,env(safe-area-inset-bottom))] text-ivory">
      <GoldLines className="absolute inset-0 -z-10 size-full opacity-70" />
      <Sparkles count={16} className="-z-10" />
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_50%_0%,rgb(201_162_75/0.18),transparent_60%)]" />
      <Brand />
      <main id="main-content" tabIndex={-1} className="w-full max-w-sm text-ink">
        {children}
      </main>
    </div>
  );
}
