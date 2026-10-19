/** Mirrors backend/app/scanning/profiles.py. The server is the authority; this only labels. */
export const SCAN_PROFILES = [
  { key: "desktop", label: "Desktop", weight: 1, note: "1280 x 800 screen, no throttling." },
  { key: "phone", label: "Small phone", weight: 1, note: "360 x 640 touch screen." },
  {
    key: "slow_network",
    label: "Slow network (3G-like)",
    weight: 2,
    note: "Throttled connection; pages get more time to load.",
  },
  {
    key: "low_end_phone",
    label: "Low-end phone on 3G",
    weight: 3,
    note: "Small screen, CPU four times slower, 3G-like connection.",
  },
] as const;

export function profileLabel(limits: Record<string, unknown> | undefined): string {
  const profile = limits?.profile as { label?: string } | undefined;
  return profile?.label ?? "Desktop";
}

export const ENGINE_LABEL: Record<string, string> = {
  chromium: "Chromium",
  firefox: "Firefox",
  webkit: "WebKit",
};
