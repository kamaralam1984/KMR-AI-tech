// Captures real screenshots of the running app for the homepage. It CREATES a throwaway user and
// organization (showcase-*@example.com), runs a REAL scan of example.com, diagnoses it, then
// screenshots the result. Development data only; `make clean-test-data` removes the user.
// Needs the API, web app, worker and scanner sandbox running.
// Usage: node scripts/make-showcase.mjs
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { chromium, request } from "playwright-core";

const baseUrl = process.env.BASE_URL ?? "http://localhost:3000";
const chromePath = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const outDir = new URL("../public/images/", import.meta.url).pathname;
const backendDir = new URL("../../backend/", import.meta.url).pathname;
mkdirSync(outDir, { recursive: true });

const suffix = Math.random().toString(36).slice(2, 10);
const api = await request.newContext({ baseURL: baseUrl });
await api.post("/api/auth/register", {
  data: {
    email: `showcase-${suffix}@example.com`,
    password: "correct horse battery staple",
    display_name: "Asha Verma",
  },
});
const csrf = (await (await api.get("/api/auth/csrf")).json()).csrf_token;
const headers = { "X-CSRF-Token": csrf };
const org = (
  await (
    await api.post("/api/organizations", { data: { name: "Northwind Studio" }, headers })
  ).json()
).id;
const base = `/api/organizations/${org}`;
const project = (
  await (await api.post(`${base}/projects`, { data: { name: "Marketing site" }, headers })).json()
).id;
await api.put(`${base}/projects/${project}/site`, {
  data: { site_url: "https://example.com/" },
  headers,
});
execFileSync(".venv/bin/python", ["-m", "app.db.dev_tools", "mark-verified", project], {
  cwd: backendDir,
});
const scan = (await (await api.post(`${base}/projects/${project}/scans`, { headers })).json()).id;
let status = "queued";
for (let i = 0; i < 120 && ["queued", "running"].includes(status); i++) {
  await new Promise((r) => setTimeout(r, 1000));
  status = (await (await api.get(`${base}/scans/${scan}`)).json()).status;
}
if (status !== "completed") {
  console.log(`FAILED: scan ended as ${status}`);
  process.exit(1);
}
const diagnosed = await (await api.post(`${base}/scans/${scan}/diagnose`, { headers })).json();
console.log("issues found:", diagnosed.issues?.length);
const cookies = (await api.storageState()).cookies;
await api.dispose();

const browser = await chromium.launch({ executablePath: chromePath, headless: true });
async function shot(file, viewport, path) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 2 });
  await context.addCookies(cookies);
  const page = await context.newPage();
  await page.goto(baseUrl + path, { waitUntil: "networkidle" });
  // The Next.js dev overlay is not part of the product, so it is hidden from the pictures.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  await page.waitForTimeout(600);
  await page.screenshot({ path: outDir + file, type: "jpeg", quality: 82 });
  await context.close();
  console.log("wrote", file);
}
await shot("product-dashboard.jpg", { width: 1280, height: 800 }, "/dashboard");
await shot("product-issues.jpg", { width: 1280, height: 800 }, "/issues");
await shot("product-phone.jpg", { width: 390, height: 844 }, `/scans/${scan}`);
await browser.close();
