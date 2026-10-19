import type { Metadata } from "next";

import { Home } from "@/components/marketing/home";

export const metadata: Metadata = {
  title: { absolute: "KMR AI · Find what's broken. Fix it with proof." },
  description: "KMR AI scans your site, shows the evidence, and changes nothing until you approve.",
};

export default function HomePage() {
  return <Home />;
}
