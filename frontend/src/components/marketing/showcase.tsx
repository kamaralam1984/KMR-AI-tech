import Image from "next/image";

import { Reveal } from "./reveal";

/** Real screenshots of the running product (made by scripts/make-showcase.mjs). */
export function Showcase() {
  return (
    <section aria-labelledby="see-title" className="relative overflow-hidden px-5 py-24">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_30%,rgb(201_162_75/0.16),transparent_60%)]" />
      <div className="relative mx-auto max-w-6xl">
        <Reveal>
          <h2 id="see-title" className="font-display max-w-2xl text-4xl font-semibold sm:text-6xl">
            See it <span className="gold-text">working.</span>
          </h2>
        </Reveal>
        <div className="relative mt-14 grid items-end gap-6 lg:grid-cols-[1.6fr_1fr]">
          <Reveal>
            <div className="relative">
              <div className="overflow-hidden rounded-xl bg-navy-900 p-2 shadow-[0_30px_80px_-20px_rgb(201_162_75/0.45)] ring-1 ring-gold-500/50">
                <Image
                  src="/images/product-dashboard.jpg"
                  alt="The KMR AI dashboard showing counts of scans, open issues and approvals"
                  width={2560}
                  height={1600}
                  sizes="(min-width: 1024px) 60vw, 100vw"
                  className="h-auto w-full rounded-lg"
                />
              </div>
              <div className="absolute -right-3 -bottom-8 hidden w-[46%] overflow-hidden rounded-xl bg-navy-900 p-1.5 shadow-2xl ring-1 ring-gold-500/50 [animation:float-slow_7s_ease-in-out_infinite] sm:block">
                <Image
                  src="/images/product-issues.jpg"
                  alt="The KMR AI issues list with one finding and severity filters"
                  width={2560}
                  height={1600}
                  sizes="30vw"
                  className="h-auto w-full rounded-md"
                />
              </div>
            </div>
          </Reveal>
          <Reveal delay={0.2}>
            <div className="mx-auto w-[min(15rem,70%)] rounded-[2rem] bg-navy-900 p-2 shadow-[0_30px_80px_-20px_rgb(201_162_75/0.45)] ring-1 ring-gold-500/60">
              <Image
                src="/images/product-phone.jpg"
                alt="A completed scan result on a phone"
                width={780}
                height={1688}
                sizes="240px"
                className="h-auto w-full rounded-[1.6rem]"
              />
            </div>
          </Reveal>
        </div>
        <p className="mt-14 text-xs text-ivory/55">
          Screenshots of KMR AI after a real scan of example.com.
        </p>
      </div>
    </section>
  );
}
