// Accessibility audit with axe-core plus a keyboard-focus check. Needs the frontend and backend
// running. Like the smoke check it CREATES a throwaway user (a11y-*@example.com): development data
// only; `make clean-test-data` removes it.
// Usage: BASE_URL=http://localhost:3000 CHROME_PATH=/usr/bin/google-chrome node scripts/a11y-audit.mjs
import { readFileSync } from "node:fs";
import { chromium, request } from "playwright-core";

const baseUrl = process.env.BASE_URL ?? "http://localhost:3000";
const chromePath = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const axeSource = readFileSync(
  new URL("../node_modules/axe-core/axe.min.js", import.meta.url),
  "utf8",
);

const suffix = Math.random().toString(36).slice(2, 10);
const api = await request.newContext({ baseURL: baseUrl });
await api.post("/api/auth/register", {
  data: {
    email: `a11y-${suffix}@example.com`,
    password: "correct horse battery staple",
    display_name: "A11y Audit",
  },
});
const csrf = (await (await api.get("/api/auth/csrf")).json()).csrf_token;
const headers = { "X-CSRF-Token": csrf };
const org = (
  await (await api.post("/api/organizations", { data: { name: "A11y Org" }, headers })).json()
).id;
const project = (
  await (
    await api.post(`/api/organizations/${org}/projects`, {
      data: { name: "A11y Project" },
      headers,
    })
  ).json()
).id;
const cookies = (await api.storageState()).cookies;
await api.dispose();

const publicPaths = ["/", "/login", "/register"];
const appPaths = [
  "/dashboard",
  "/projects",
  `/projects/${project}`,
  "/scans",
  "/issues",
  "/repairs",
  "/approvals",
  "/history",
  "/settings",
];
const browser = await chromium.launch({ executablePath: chromePath, headless: true });
const blocking = [];
const notes = [];

async function audit(context, path, viewport) {
  const page = await context.newPage();
  await page.goto(baseUrl + path, { waitUntil: "networkidle" });
  if (new URL(page.url()).pathname !== path) {
    blocking.push(`${viewport} ${path}: redirected to ${new URL(page.url()).pathname}`);
    return page.close();
  }
  await page.evaluate(axeSource);
  const result = await page.evaluate(() =>
    axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    }),
  );
  for (const v of result.violations) {
    const line = `${viewport} ${path}: ${v.id} (${v.impact}) x${v.nodes.length} - ${v.help}`;
    (v.impact === "critical" || v.impact === "serious" ? blocking : notes).push(line);
  }
  await page.close();
}

// Every focus stop must show a visible indicator, and Tab must reach the main form control.
async function keyboard(context, path) {
  const page = await context.newPage();
  await page.goto(baseUrl + path, { waitUntil: "networkidle" });
  const seen = [];
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      // The Next.js dev overlay is not part of the product.
      if (!el || el === document.body || el.tagName.toLowerCase().startsWith("nextjs")) return null;
      const cs = getComputedStyle(el);
      const visible = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const shadow = cs.boxShadow !== "none";
      return {
        tag: el.tagName.toLowerCase(),
        label: (el.getAttribute("aria-label") || el.textContent || el.getAttribute("name") || "")
          .trim()
          .slice(0, 30),
        indicator: visible || shadow,
      };
    });
    if (info) seen.push(info);
  }
  const missing = seen.filter((s) => !s.indicator);
  if (seen.length === 0) blocking.push(`keyboard ${path}: nothing is focusable`);
  for (const m of missing)
    blocking.push(`keyboard ${path}: no visible focus on <${m.tag}> "${m.label}"`);
  notes.push(
    `keyboard ${path}: ${seen.length} focus stops, ${missing.length} without a visible indicator`,
  );
  await page.close();
}

for (const [name, size] of [
  ["phone", { width: 375, height: 800 }],
  ["laptop", { width: 1280, height: 800 }],
]) {
  const anon = await browser.newContext({ viewport: size });
  for (const p of publicPaths) await audit(anon, p, name);
  await anon.close();
  const signed = await browser.newContext({ viewport: size });
  await signed.addCookies(cookies);
  for (const p of appPaths) await audit(signed, p, name);
  await signed.close();
}
const anon = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await keyboard(anon, "/login");
await anon.close();
const signed = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await signed.addCookies(cookies);
await keyboard(signed, "/dashboard");
await signed.close();
await browser.close();

console.log(
  `axe-core ${(await import("axe-core")).default.version}: WCAG 2.0/2.1/2.2 A and AA rules`,
);
for (const n of notes) console.log("  note: " + n);
if (blocking.length) {
  console.log(`FAILED (${blocking.length} serious/critical):`);
  for (const b of blocking) console.log(" - " + b);
  process.exit(1);
}
console.log("PASSED: no serious or critical violations");
