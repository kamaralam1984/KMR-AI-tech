// Renders the app icons from an inline SVG using the system Chrome. Run once; output is committed.
// Usage: CHROME_PATH=/usr/bin/google-chrome node scripts/make-icons.mjs
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";

const outDir = new URL("../public/", import.meta.url).pathname;
mkdirSync(outDir, { recursive: true });

// A magnifier over a page: scanning. Keep the glyph inside the central 60% so the maskable
// variant survives circular and squircle cropping.
const glyph = `
  <g fill="none" stroke="url(#g)" stroke-width="22" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="236" cy="236" r="104"/>
    <path d="M312 312 L392 392"/>
    <path d="M196 236 h80 M236 196 v80" stroke="#f1d98f"/>
  </g>`;
const svg = (radius) => `
  <svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f6e3a8"/><stop offset="1" stop-color="#b8893a"/></linearGradient></defs>
    <rect width="512" height="512" rx="${radius}" fill="#0e0d0b"/>${glyph}
  </svg>`;

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? "/usr/bin/google-chrome",
  headless: true,
});
const targets = [
  { file: "icon-512.png", size: 512, radius: 112 },
  { file: "icon-192.png", size: 192, radius: 42 },
  { file: "apple-touch-icon.png", size: 180, radius: 0 }, // iOS rounds corners itself
  { file: "icon-maskable-512.png", size: 512, radius: 0 }, // full-bleed for platform masks
];
for (const { file, size, radius } of targets) {
  const page = await browser.newPage({ viewport: { width: size, height: size } });
  await page.setContent(
    `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg(radius)}`,
  );
  await page.screenshot({ path: outDir + file, omitBackground: true });
  await page.close();
  console.log("wrote", file);
}
await browser.close();
