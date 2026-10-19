// Responsive smoke check. Needs the frontend and the backend already running.
// It CREATES one throwaway user and organization (smoke-*@example.com) in the database the API
// uses, so run it against development data only. `make clean-test-data` removes them.
// Set SMOKE_SCAN=1 to also run a real scan of example.com and check the scan result page; that
// needs the worker and the scanner sandbox running (`make worker`, `make scanner-up`).
// Usage: BASE_URL=http://localhost:3000 CHROME_PATH=/usr/bin/google-chrome node scripts/responsive-smoke.mjs
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { chromium, request } from "playwright-core";

const baseUrl = process.env.BASE_URL ?? "http://localhost:3000";
const chromePath = process.env.CHROME_PATH ?? "/usr/bin/google-chrome";
const outDir = new URL("../../.artifacts/responsive/", import.meta.url).pathname;
const backendDir = new URL("../../backend/", import.meta.url).pathname;

const publicPaths = ["/", "/login", "/register"];
const appPaths = [
  "/dashboard",
  "/projects",
  "/scans",
  "/issues",
  "/repairs",
  "/approvals",
  "/history",
  "/settings",
];
const viewports = [
  { name: "phone", width: 375, height: 800 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "wide", width: 1920, height: 1080 },
];

// Sign up a throwaway user through the API (no browser Origin header, so no CSRF needed) and
// give it an organization, so the app pages render real content rather than onboarding only.
const suffix = Math.random().toString(36).slice(2, 10);
const api = await request.newContext({ baseURL: baseUrl });
const register = await api.post("/api/auth/register", {
  data: {
    email: `smoke-${suffix}@example.com`,
    password: "correct horse battery staple",
    display_name: "Smoke Test",
  },
});
if (register.status() !== 201) {
  console.log(`FAILED: could not create the smoke user (HTTP ${register.status()}).`);
  process.exit(1);
}
const csrf = (await (await api.get("/api/auth/csrf")).json()).csrf_token;
const org = await api.post("/api/organizations", {
  data: { name: "Smoke Org" },
  headers: { "X-CSRF-Token": csrf },
});
if (org.status() !== 201) {
  console.log(`FAILED: could not create the smoke organization (HTTP ${org.status()}).`);
  process.exit(1);
}
const orgId = (await org.json()).id;
const headers = { "X-CSRF-Token": csrf };
const project = await api.post(`/api/organizations/${orgId}/projects`, {
  data: { name: "Smoke Project" },
  headers,
});
const projectId = (await project.json()).id;
appPaths.push(`/projects/${projectId}`);

if (process.env.SMOKE_SCAN === "1") {
  const base = `/api/organizations/${orgId}`;
  await api.put(`${base}/projects/${projectId}/site`, {
    data: { site_url: "https://example.com/" },
    headers,
  });
  // Nobody here owns example.com; the local-only dev tool records the proof (see its docstring).
  execFileSync(".venv/bin/python", ["-m", "app.db.dev_tools", "mark-verified", projectId], {
    cwd: backendDir,
  });
  const started = await api.post(`${base}/projects/${projectId}/scans`, { headers });
  if (started.status() !== 202) {
    console.log(`FAILED: could not start the smoke scan (HTTP ${started.status()}).`);
    process.exit(1);
  }
  const scanId = (await started.json()).id;
  let status = "queued";
  for (let i = 0; i < 120 && ["queued", "running"].includes(status); i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    status = (await (await api.get(`${base}/scans/${scanId}`)).json()).status;
  }
  if (status !== "completed") {
    console.log(`FAILED: the smoke scan ended as "${status}" (is the worker running?).`);
    process.exit(1);
  }
  appPaths.push(`/scans/${scanId}`);
}
const sessionCookies = (await api.storageState()).cookies;
await api.dispose();

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ executablePath: chromePath, headless: true });
const failures = [];
let checks = 0;
let pages = 0;

async function checkPage(context, vp, path) {
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  const label = `${vp.name} ${vp.width}px ${path}`;
  try {
    const response = await page.goto(baseUrl + path, { waitUntil: "networkidle" });
    pages++;
    checks++;
    if (!response || response.status() !== 200)
      failures.push(`${label}: HTTP ${response?.status()}`);
    // A redirect (for example to /login) would silently test the wrong page.
    if (new URL(page.url()).pathname !== path) {
      failures.push(`${label}: ended up on ${new URL(page.url()).pathname}`);
    }

    const result = await page.evaluate(() => {
      const doc = document.documentElement;
      const h1 = document.querySelector("h1");
      const box = h1?.getBoundingClientRect();
      // Elements wider than the viewport that are not inside a scroll container.
      const overflowing = [...document.querySelectorAll("body *")]
        .filter((el) => {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.right <= window.innerWidth + 1) return false;
          for (let p = el.parentElement; p; p = p.parentElement) {
            const o = getComputedStyle(p).overflowX;
            if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return false;
          }
          return true;
        })
        .slice(0, 3)
        .map((el) => el.tagName.toLowerCase() + "." + String(el.className).slice(0, 60));
      return {
        scrollWidth: doc.scrollWidth,
        innerWidth: window.innerWidth,
        h1Text: h1?.textContent?.trim() ?? null,
        h1Visible:
          !!box &&
          box.width > 0 &&
          box.height > 0 &&
          box.left >= 0 &&
          box.right <= window.innerWidth + 1,
        h1Clipped: !!h1 && h1.scrollWidth > h1.clientWidth + 1,
        overflowing,
      };
    });
    checks++;
    if (result.scrollWidth > result.innerWidth) {
      failures.push(`${label}: horizontal scroll (${result.scrollWidth} > ${result.innerWidth})`);
    }
    if (result.overflowing.length) {
      failures.push(`${label}: elements past viewport: ${result.overflowing.join(", ")}`);
    }
    if (!result.h1Text) failures.push(`${label}: no <h1>`);
    else if (!result.h1Visible || result.h1Clipped) {
      failures.push(`${label}: <h1> clipped or outside viewport`);
    }
    if (consoleErrors.length) {
      failures.push(`${label}: console errors: ${consoleErrors.join(" | ").slice(0, 200)}`);
    }
    await page.screenshot({
      path: `${outDir}${vp.name}${path.replaceAll("/", "-")}.png`,
      fullPage: true,
    });
  } catch (error) {
    failures.push(`${label}: ${error.message}`);
  }
  await page.close();
}

for (const vp of viewports) {
  const size = { width: vp.width, height: vp.height };
  // Signed out for the public pages, signed in for the app pages.
  const anonymous = await browser.newContext({ viewport: size });
  for (const path of publicPaths) await checkPage(anonymous, vp, path);
  await anonymous.close();

  const signedIn = await browser.newContext({ viewport: size });
  await signedIn.addCookies(sessionCookies);
  for (const path of appPaths) await checkPage(signedIn, vp, path);
  await signedIn.close();
}
await browser.close();

console.log(`${pages} page/viewport combinations, ${checks} checks`);
if (failures.length) {
  console.log(`FAILED (${failures.length}):`);
  for (const f of failures) console.log(" - " + f);
  process.exit(1);
}
console.log("PASSED: no horizontal overflow, no clipped headings, no console errors");
console.log(`Screenshots: ${outDir}`);
