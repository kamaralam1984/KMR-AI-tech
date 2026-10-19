import { Eye, ScrollText, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { Brand } from "@/components/layout/brand";

import { GoldLines } from "./gold-lines";
import { ImageSlot } from "./image-slot";
import { Reveal } from "./reveal";
import { Showcase } from "./showcase";
import { Sparkles } from "./sparkles";
import { Spotlight } from "./spotlight";

const STEPS = [
  { name: "scan", title: "Scan", line: "Read-only. Your site is never touched." },
  { name: "highlight", title: "Highlight", line: "Every issue, shown with its evidence." },
  { name: "approve", title: "Approve", line: "Your yes, tied to one exact change." },
  { name: "verify", title: "Verify", line: "Scan again. Fixed, or not." },
] as const;

const PROMISES = [
  {
    icon: Eye,
    title: "Evidence, not guesses",
    line: "What was seen stays apart from what is assumed.",
  },
  {
    icon: ShieldCheck,
    title: "Nothing moves without you",
    line: "No merge, no deploy. Approval expires and can be revoked.",
  },
  {
    icon: ScrollText,
    title: "A record you can trust",
    line: "Every decision is written to a tamper-evident log.",
  },
] as const;

const GOLD_LINK =
  "inline-flex h-12 items-center justify-center rounded-md px-7 text-sm font-semibold transition";

export function Home() {
  return (
    <div className="bg-navy-950 text-ivory">
      <header className="absolute inset-x-0 top-0 z-20">
        <nav
          aria-label="Site"
          className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5 pt-[max(1.25rem,env(safe-area-inset-top))]"
        >
          <span className="text-ivory">
            <Brand />
          </span>
          <div className="flex items-center gap-1 sm:gap-3">
            <a
              href="#how"
              className="hidden min-h-11 items-center px-3 text-sm text-ivory/80 hover:text-gold-300 sm:inline-flex"
            >
              How it works
            </a>
            <Link
              href="/login"
              className="inline-flex min-h-11 items-center px-3 text-sm text-ivory/90 hover:text-gold-300"
            >
              Sign in
            </Link>
            <Link href="/register" className={`${GOLD_LINK} gold-button h-10 px-4`}>
              Get started
            </Link>
          </div>
        </nav>
      </header>

      <main id="main-content" tabIndex={-1}>
        {/* Hero */}
        <Spotlight className="overflow-hidden">
          <section className="relative isolate flex min-h-[100svh] items-center">
            <GoldLines className="absolute inset-0 -z-10 size-full opacity-90" />
            <Sparkles count={26} className="-z-10" />
            <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_30%_20%,rgb(201_162_75/0.14),transparent_55%)]" />
            <div className="mx-auto w-full max-w-6xl px-5 pt-28 pb-20">
              <Reveal>
                <p className="mb-5 text-xs tracking-[0.3em] text-gold-400 uppercase">
                  Website scanning, with proof
                </p>
              </Reveal>
              <Reveal delay={0.1}>
                <h1 className="font-display max-w-4xl text-5xl leading-[1.02] font-semibold sm:text-7xl lg:text-8xl">
                  Find what&apos;s broken.
                  <span className="gold-text block">Fix it with proof.</span>
                </h1>
              </Reveal>
              <Reveal delay={0.25}>
                <p className="mt-7 max-w-xl text-lg text-ivory/75">
                  KMR AI scans your site, shows the evidence, and changes nothing until you approve.
                </p>
              </Reveal>
              <Reveal delay={0.4}>
                <div className="mt-10 flex flex-wrap items-center gap-4">
                  <Link href="/register" className={`${GOLD_LINK} gold-button`}>
                    Start for free
                  </Link>
                  <Link
                    href="/login"
                    className={`${GOLD_LINK} border border-gold-500/50 text-gold-300 hover:bg-gold-500/10`}
                  >
                    Sign in
                  </Link>
                </div>
              </Reveal>
            </div>
          </section>
        </Spotlight>

        <Showcase />

        {/* Steps: image first, words second */}
        <section id="how" aria-labelledby="how-title" className="relative px-5 py-24">
          <div className="mx-auto max-w-6xl">
            <Reveal>
              <h2
                id="how-title"
                className="font-display max-w-2xl text-4xl font-semibold sm:text-6xl"
              >
                Four calm steps.
              </h2>
            </Reveal>
            <ol className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {STEPS.map((step, i) => (
                <li key={step.name}>
                  <Reveal delay={i * 0.12}>
                    <figure className="group">
                      <div className="relative aspect-[4/3] overflow-hidden rounded-xl ring-1 ring-gold-500/30 transition duration-500 group-hover:ring-gold-400/80 group-hover:shadow-[0_0_40px_-8px_rgb(201_162_75/0.5)]">
                        <ImageSlot
                          name={step.name}
                          alt={`${step.title}: illustration`}
                          sizes="(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw"
                        />
                        <span className="pointer-events-none absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-white/15 to-transparent opacity-0 group-hover:opacity-100 group-hover:[animation:sweep_1.4s_ease-out]" />
                      </div>
                      <figcaption className="mt-4">
                        <span className="text-xs text-gold-400">0{i + 1}</span>
                        <h3 className="font-display text-3xl font-semibold">{step.title}</h3>
                        <p className="mt-1 text-sm text-ivory/70">{step.line}</p>
                      </figcaption>
                    </figure>
                  </Reveal>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Promises */}
        <section aria-labelledby="trust-title" className="relative overflow-hidden px-5 py-24">
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-gold-500/70 to-transparent" />
          <div className="mx-auto max-w-6xl">
            <Reveal>
              <h2
                id="trust-title"
                className="font-display max-w-2xl text-4xl font-semibold sm:text-6xl"
              >
                Built to be <span className="gold-text">trusted.</span>
              </h2>
            </Reveal>
            <ul className="mt-14 grid gap-px overflow-hidden rounded-xl bg-gold-500/25 sm:grid-cols-3">
              {PROMISES.map(({ icon: Icon, title, line }, i) => (
                <li key={title} className="bg-navy-900 p-8">
                  <Reveal delay={i * 0.12}>
                    <Icon className="size-7 text-gold-400" aria-hidden="true" />
                    <h3 className="font-display mt-5 text-2xl font-semibold">{title}</h3>
                    <p className="mt-2 text-sm text-ivory/70">{line}</p>
                  </Reveal>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Closing */}
        <section
          aria-labelledby="cta-title"
          className="relative isolate overflow-hidden px-5 py-28 text-center"
        >
          <Sparkles count={18} className="-z-10" />
          <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_50%_100%,rgb(201_162_75/0.22),transparent_60%)]" />
          <Reveal>
            <h2
              id="cta-title"
              className="font-display mx-auto max-w-3xl text-5xl font-semibold sm:text-7xl"
            >
              Ready when <span className="gold-text">you are.</span>
            </h2>
          </Reveal>
          <Reveal delay={0.2}>
            <Link href="/register" className={`${GOLD_LINK} gold-button mt-10`}>
              Create your workspace
            </Link>
          </Reveal>
        </section>
      </main>

      <footer className="border-t border-gold-500/25 px-5 py-8 pb-[max(2rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 text-xs text-ivory/60">
          <span>© KMR AI</span>
          <span>Scans are read-only. Nothing is applied without your approval.</span>
        </div>
      </footer>
    </div>
  );
}
