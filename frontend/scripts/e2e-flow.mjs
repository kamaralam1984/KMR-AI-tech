// End-to-end check of the main flow in a phone-sized browser, against a running stack.
// It CREATES real users and organizations in whatever database the API uses (random emails),
// so point it at a development database only.
// Open the app as localhost (not 127.0.0.1): the API only accepts that browser origin.
// Usage: BASE_URL=http://localhost:3000 CHROME_PATH=/usr/bin/google-chrome node scripts/e2e-flow.mjs
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { chromium, request } from "playwright-core";

const baseUrl = process.env.BASE_URL ?? "http://localhost:3000";
const outDir = new URL("../../.artifacts/e2e/", import.meta.url).pathname;
const backendDir = new URL("../../backend/", import.meta.url).pathname;
// Start from an empty screenshot folder so old failures are not mistaken for new ones.
rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

const suffix = Math.random().toString(36).slice(2, 10);
const owner = { name: "E2E Owner", email: `e2e-owner-${suffix}@example.com` };
const teammate = { name: "E2E Teammate", email: `e2e-mate-${suffix}@example.com` };
const password = "correct horse battery staple";

// A second account to add as a member, created directly through the API.
const api = await request.newContext({ baseURL: baseUrl });
const created = await api.post("/api/auth/register", {
  data: { email: teammate.email, password, display_name: teammate.name },
});
if (created.status() !== 201) throw new Error(`could not create teammate: ${created.status()}`);
await api.dispose();

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ?? "/usr/bin/google-chrome",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));

const problems = [];
const passed = [];
let shot = 0;

async function step(name, fn) {
  try {
    await fn();
    passed.push(name);
  } catch (error) {
    problems.push(`${name}: ${String(error.message).split("\n")[0]}`);
    await page
      .screenshot({ path: `${outDir}FAILED-${++shot}.png`, fullPage: true })
      .catch(() => {});
    throw error;
  }
}

async function layoutChecks(label) {
  const result = await page.evaluate(() => {
    const small = [...document.querySelectorAll("a, button, input:not([type=checkbox]), select")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return (
          r.width > 2 && r.height > 2 && style.visibility !== "hidden" && style.display !== "none"
        );
      })
      .filter((el) => el.getBoundingClientRect().height < 40 && !el.closest("[hidden]"))
      .map(
        (el) =>
          `${el.tagName.toLowerCase()}:${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 24)}`,
      );
    return {
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      small,
    };
  });
  if (result.overflow > 0) problems.push(`${label}: horizontal overflow ${result.overflow}px`);
  if (result.small.length)
    problems.push(`${label}: touch targets under 40px tall: ${result.small.join(", ")}`);
}

async function snap(name) {
  await page.screenshot({
    path: `${outDir}${String(++shot).padStart(2, "0")}-${name}.png`,
    fullPage: true,
  });
}

try {
  await step("signed-out visit to /projects redirects to sign-in with ?next=", async () => {
    await page.goto(`${baseUrl}/projects`);
    await page.waitForURL(/\/login\?next=%2Fprojects/);
  });

  await step("register from the sign-in page", async () => {
    await page.getByRole("link", { name: "Create an account" }).click();
    await page.waitForURL("**/register");
    await layoutChecks("register");
    await snap("register");
    await page.getByLabel("Your name").fill(owner.name);
    await page.getByLabel("Work email").fill(owner.email);
    // Next.js keeps the previous page (sign-in) hidden in the DOM, so pick the visible field.
    await page.locator('input[name="password"]:visible').fill(password);
    await page.getByRole("button", { name: "Create account" }).click();
    await page.waitForURL("**/dashboard");
  });

  await step("new user sees onboarding, creates an organization", async () => {
    await page.getByRole("heading", { name: "Create your organization" }).waitFor();
    await snap("onboarding");
    await page.getByLabel("Organization name").fill("Acme E2E");
    await page.getByRole("button", { name: "Create organization" }).click();
    await page.getByRole("heading", { name: "Acme E2E" }).waitFor();
    await page.getByText("Owner", { exact: true }).first().waitFor();
    await layoutChecks("dashboard");
    await snap("dashboard");
  });

  await step("bottom tab bar is visible and navigates (app-style)", async () => {
    const nav = page.getByRole("navigation", { name: "Primary" });
    await nav.waitFor();
    const box = await nav.boundingBox();
    if (!box || box.y + box.height < 800) throw new Error("tab bar is not pinned to the bottom");
    await nav.getByRole("link", { name: /Projects/ }).click();
    await page.waitForURL("**/projects");
  });

  await step("create and rename a project", async () => {
    await page.getByLabel("Project name").fill("Site One");
    await page.getByRole("button", { name: "Create project" }).click();
    await page.getByText("Site One").waitFor();
    await layoutChecks("projects");
    await snap("projects");
    await page.getByRole("button", { name: "Rename Site One" }).click();
    const dialog = page.getByRole("dialog", { name: "Rename project" });
    await dialog.getByLabel("Project name").fill("Site Renamed");
    await dialog.getByRole("button", { name: "Save" }).click();
    await page.getByText("Site Renamed").waitFor();
  });

  let projectId = "";
  await step(
    "project page: unsafe address refused, site saved, scan blocked until verified",
    async () => {
      await page.getByRole("link", { name: "Site Renamed" }).click();
      await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
      projectId = new URL(page.url()).pathname.split("/").pop();
      await page.getByLabel("Website address").fill("http://10.0.0.1/");
      await page.getByRole("button", { name: "Save website" }).click();
      await page.getByText(/private network/).waitFor();
      await page.getByLabel("Website address").fill("https://example.com/");
      await page.getByRole("button", { name: "Save website" }).click();
      await page.getByText("Not verified yet").waitFor();
      await page.getByText("_kmr-verify.example.com").waitFor();
      const start = page.getByRole("button", { name: /Start scan/ });
      if (!(await start.isDisabled()))
        throw new Error("Start scan must be disabled until ownership is verified");
      await page.getByText("Verify that you own the website first.").waitFor();
      await layoutChecks("project page");
      await snap("project-unverified");
    },
  );

  await step("a failed ownership check says so and verifies nothing", async () => {
    await page.getByRole("button", { name: "Check DNS record" }).click();
    await page.getByRole("alert").filter({ hasText: "not confirmed" }).waitFor();
    if (!(await page.getByRole("button", { name: /Start scan/ }).isDisabled())) {
      throw new Error("a failed check must not enable scanning");
    }
  });

  await step("verified (dev tool, local only): ownership is recorded", async () => {
    // Nobody here owns example.com, so the ownership proof is recorded by the local-only dev
    // tool. It refuses to run unless KMR_APP_ENV=local; the API has no such path.
    execFileSync(".venv/bin/python", ["-m", "app.db.dev_tools", "mark-verified", projectId], {
      cwd: backendDir,
    });
    await page.reload();
    await page.getByText(/Verified by DNS/).waitFor();
  });

  async function addCheck(name, path) {
    await page.getByRole("button", { name: "Add an API check" }).click();
    const dialog = page.getByRole("dialog", { name: "Add an API check" });
    await dialog.getByLabel(/^Name/).fill(name);
    await dialog.getByLabel(/Path on your website/).fill(path);
    await dialog.getByRole("button", { name: "Add check" }).click();
    await page.getByRole("listitem").filter({ hasText: name }).first().waitFor();
  }

  await step(
    "API checks: add a good and a failing check, run them, see plain verdicts",
    async () => {
      await addCheck("Home page", "/");
      await addCheck("Missing page", "/definitely-missing");
      const home = page.getByRole("listitem").filter({ hasText: "Home page" });
      await home.getByRole("button", { name: /Run Home page now/ }).click();
      await home.getByText("Passed", { exact: true }).waitFor();
      const missing = page.getByRole("listitem").filter({ hasText: "Missing page" });
      await missing.getByRole("button", { name: /Run Missing page now/ }).click();
      await missing.getByText("Failed", { exact: true }).waitFor();
      await missing.getByText(/expected HTTP 200, got 404/).waitFor();
      await layoutChecks("api checks");
      await snap("api-checks");
    },
  );

  await step(
    "a real scan of example.com completes and includes the API check results",
    async () => {
      await page.getByRole("button", { name: /Start scan/ }).click();
      await page.waitForURL(/\/scans\/[0-9a-f-]{36}$/);
      await snap("scan-started");
      await page.getByText("Completed", { exact: true }).waitFor({ timeout: 120_000 });
      await page.getByText(/“Example Domain”, HTTP 200/).waitFor();
      await page.getByText(/API check “Home page” passed/).waitFor();
      await page.getByText(/API check “Missing page” failed: expected HTTP 200, got 404/).waitFor();
      await layoutChecks("scan page");
      await snap("scan-result");
    },
  );

  await step(
    "backend evidence: sources are honest, a failed API check is shown as one",
    async () => {
      const sources = page.getByRole("list", { name: "Evidence sources" });
      await sources.waitFor();
      await sources.getByText("API checks (observed by KMR AI)").waitFor();
      await sources.getByText("Monitoring (Sentry, Datadog, OpenTelemetry)").waitFor();
      if ((await sources.getByText("Not connected").count()) < 3) {
        throw new Error("monitoring, repository and logs should be shown as not connected");
      }
      await page.getByText("API check failed", { exact: true }).waitFor();
      await snap("backend-evidence");
    },
  );

  await step(
    "uploading backend logs makes them a source, and they can be removed again",
    async () => {
      await page.getByRole("button", { name: /Upload backend logs/ }).click();
      const dialog = page.getByRole("dialog", { name: "Upload backend logs" });
      await dialog
        .getByLabel("Log lines")
        .fill('{"request_id":"kmr-demo-1","status":500,"path":"/x"}');
      await dialog.getByRole("button", { name: "Add logs" }).click();
      await page.getByText("1 log line added").waitFor();
      await page.getByText("1 log entries uploaded.").waitFor();
      await page.getByRole("button", { name: "Remove uploaded logs" }).click();
      await page.getByText("No backend logs were uploaded for this scan.").waitFor();
    },
  );

  await step("the scan's screenshot opens and is a real image", async () => {
    await page
      .getByRole("button", { name: /View screenshot of/ })
      .first()
      .click();
    const image = page.getByRole("dialog", { name: "Screenshot" }).getByRole("img");
    await image.waitFor();
    await page.waitForFunction(() => {
      const img = document.querySelector("dialog[open] img");
      return img instanceof HTMLImageElement && img.complete && img.naturalWidth > 100;
    });
    await snap("scan-screenshot");
    await page.keyboard.press("Escape");
  });

  await step("the scan shows in the project history and the Scans list", async () => {
    await page.goto(`${baseUrl}/projects/${projectId}`);
    await page.getByRole("heading", { name: "Scan history" }).waitFor();
    await page
      .getByRole("link", { name: /example\.com/ })
      .first()
      .waitFor();
    await page.goto(`${baseUrl}/scans`);
    await page
      .getByRole("link", { name: /example\.com/ })
      .first()
      .waitFor();
    await layoutChecks("scans list");
    await snap("scans-list");
  });

  await step("diagnose the scan: issues cite evidence and say who wrote them", async () => {
    await page.goto(`${baseUrl}/scans`);
    await page
      .getByRole("link", { name: /example\.com/ })
      .first()
      .click();
    await page.getByRole("button", { name: /Diagnose this scan/ }).click();
    const list = page.getByRole("list", { name: "Issues from this scan" });
    await list.waitFor();
    await page
      .getByText(/built-in rules, not by AI/)
      .first()
      .waitFor();
    await layoutChecks("scan with issues");
    await list.getByRole("link").first().click();
    await page.getByRole("heading", { level: 2, name: "What we know" }).waitFor();
    await page.getByRole("region", { name: "Observed" }).waitFor();
    await page.getByRole("region", { name: "Not yet verified" }).waitFor();
    await page.getByText("No repository connected").waitFor();
    await layoutChecks("issue detail");
    await snap("issue-detail");
    await page.goto(`${baseUrl}/issues`);
    await page
      .getByRole("link", { name: /main heading|language|viewport|title/i })
      .first()
      .waitFor();
  });

  await step("dashboard counts and the audit history match what just happened", async () => {
    await page.goto(`${baseUrl}/dashboard`);
    const open = page.locator("dt", { hasText: "Open issues" }).locator("..");
    await open
      .getByText(/^[1-9]\d*$/)
      .first()
      .waitFor();
    await page.getByText("Counts come from your stored records").waitFor();
    await page.goto(`${baseUrl}/history`);
    await page.getByText("Chain intact").waitFor();
    await page.getByRole("cell", { name: "Organization created" }).first().waitFor();
    await layoutChecks("history");
    await snap("history");
  });

  await step("settings: add a member, change their role, remove them", async () => {
    await page.goto(`${baseUrl}/settings`);
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: /Settings/ })
      .click();
    await page.waitForURL("**/settings");
    await page.getByLabel("Add by email").fill(teammate.email);
    await page.getByRole("button", { name: "Add member" }).click();
    await page.getByText(teammate.email).waitFor();
    await layoutChecks("settings");
    await snap("settings");
    await page.getByLabel(`Role for ${teammate.email}`).selectOption("viewer");
    await page.getByText("Role updated").waitFor();
    await page.getByRole("button", { name: `Remove ${teammate.email}` }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
    await page.getByText("Member removed").waitFor();
  });

  await step("last owner cannot leave the organization", async () => {
    await page.getByRole("button", { name: "Leave organization" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Leave" }).click();
    await page.getByText("An organization must keep at least one owner.").waitFor();
  });

  await step("More opens the full navigation drawer with every page", async () => {
    await page.getByRole("button", { name: "More" }).click();
    const drawer = page.getByRole("dialog", { name: "Navigation" });
    for (const name of ["Issues", "Repairs", "Approvals", "History & audit"]) {
      await drawer.getByRole("link", { name: new RegExp(name) }).waitFor();
    }
    await snap("drawer");
    await drawer.getByRole("link", { name: /Approvals/ }).click();
    await page.getByRole("heading", { level: 1, name: "Approvals" }).waitFor();
  });

  await step("sign out, then protected pages redirect to sign-in", async () => {
    await page.getByRole("button", { name: "Account" }).click();
    await page
      .getByRole("dialog", { name: "Account" })
      .getByRole("button", { name: "Sign out", exact: true })
      .click();
    await page.waitForURL("**/login");
    await page.goto(`${baseUrl}/dashboard`);
    await page.waitForURL(/\/login/);
  });

  await step("wrong password shows a generic error, right password signs in", async () => {
    await page.getByLabel("Email").fill(owner.email);
    await page.locator('input[name="password"]:visible').fill("not the right password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("alert").filter({ hasText: "Invalid email or password." }).waitFor();
    await snap("login-error");
    await page.locator('input[name="password"]:visible').fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/dashboard/);
    await page.getByRole("heading", { name: "Acme E2E" }).waitFor();
  });

  await step("the app is installable: manifest and icons are served", async () => {
    const manifest = await (await context.request.get(`${baseUrl}/manifest.webmanifest`)).json();
    if (manifest.display !== "standalone") throw new Error("display is not standalone");
    for (const icon of manifest.icons) {
      const response = await context.request.get(baseUrl + icon.src);
      if (response.status() !== 200) throw new Error(`${icon.src} -> ${response.status()}`);
    }
  });
} catch {
  // the failing step is already recorded
}

await browser.close();
// The flow deliberately triggers three failed requests: a 409 (the last owner trying to leave),
// a 401 (wrong password) and a 422 (an unsafe website address). Browsers log those as console errors. Allow each once;
// anything else is a real problem.
for (const expected of ["status of 409", "status of 401", "status of 422"]) {
  const index = consoleErrors.findIndex((text) => text.includes(expected));
  if (index >= 0) consoleErrors.splice(index, 1);
}
if (consoleErrors.length)
  problems.push(`console errors: ${consoleErrors.slice(0, 3).join(" | ").slice(0, 300)}`);
console.log(`${passed.length} steps passed:`);
for (const name of passed) console.log("  ✓ " + name);
if (problems.length) {
  console.log(`PROBLEMS (${problems.length}):`);
  for (const p of problems) console.log("  - " + p);
  process.exit(1);
}
console.log(`ALL CHECKS PASSED. Screenshots: ${outDir}`);
