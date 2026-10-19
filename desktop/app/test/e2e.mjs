// Full stack, for real: the Electron chat window, the Python agent reading a real GTK window through
// the accessibility bus, and the running KMR AI server (`make dev-api`). Prints PASS/FAIL lines.
// Test data: a throwaway e2e-owner-*@example.com account (`make clean-test-data` removes it).
import { _electron as electron } from "playwright-core";
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const API = process.env.KMR_API || "http://localhost:8000";
const SHOT = process.env.KMR_SHOT || "";
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${!ok && detail ? `  [${detail}]` : ""}`);
};

async function account() {
  const email = `e2e-owner-${Math.random().toString(16).slice(2, 12)}@example.com`;
  let cookie = "";
  const call = async (method, p, body, csrf) => {
    const r = await fetch(API + p, {
      method,
      headers: { "Content-Type": "application/json", ...(cookie ? { cookie } : {}), ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const set = r.headers.getSetCookie?.() || [];
    if (set.length) cookie = set.map((c) => c.split(";")[0]).join("; ");
    return { status: r.status, body: await r.json().catch(() => ({})) };
  };
  const reg = await call("POST", "/v1/auth/register", { email, password: "correct horse battery staple", display_name: "E2E" });
  if (reg.status !== 201) throw new Error(`register failed: ${reg.status}`);
  const csrf = (await call("GET", "/v1/auth/csrf")).body.csrf_token;
  const org = (await call("POST", "/v1/organizations", { name: "E2E desktop" }, csrf)).body.id;
  const made = await call("POST", `/v1/organizations/${org}/extension-tokens`, { name: "e2e", days: 1, kind: "desktop" }, csrf);
  if (made.status !== 201) throw new Error(`token failed: ${made.status} ${JSON.stringify(made.body)}`);
  return made.body.token;
}

function startTestWindow() {
  // Linux: a GTK window; Windows: a Windows Forms window. Same title and the same controls.
  const helpers = path.join(ROOT, "..", "agent", "tests", "helpers");
  let child;
  if (process.platform === "win32") {
    child = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(helpers, "winforms_test_app.ps1"), "-Seconds", "150"], { stdio: ["ignore", "pipe", "ignore"] });
  } else {
    const env = { ...process.env, XDG_DATA_DIRS: "/usr/local/share:/usr/share" };
    for (const k of ["GSETTINGS_SCHEMA_DIR", "GTK_PATH", "GTK_EXE_PREFIX", "GIO_MODULE_DIR"]) delete env[k];
    child = spawn("/usr/bin/python3", [path.join(helpers, "gtk_test_app.py"), "150"], { env, stdio: ["ignore", "pipe", "ignore"] });
  }
  const events = [];
  const rl = readline.createInterface({ input: child.stdout });
  const ready = new Promise((resolve) => rl.on("line", (line) => (line === "READY" ? resolve() : line.startsWith("EVENT") && events.push(line))));
  return { child, events, ready };
}
const settle = (ms) => new Promise((r) => setTimeout(r, ms));

const token = await account();
const gtk = startTestWindow();
await gtk.ready;
await settle(1500);

const home = mkdtempSync(path.join(tmpdir(), "kmr-app-e2e-"));
const env = { ...process.env, XDG_CONFIG_HOME: home, APPDATA: home, XDG_DATA_DIRS: "/usr/local/share:/usr/share", KMR_APP_HIDDEN: "0" };
for (const k of ["GSETTINGS_SCHEMA_DIR", "GTK_PATH", "GTK_EXE_PREFIX", "GIO_MODULE_DIR", "ELECTRON_RUN_AS_NODE"]) delete env[k];
const app = await electron.launch({
  executablePath: require("electron"),
  args: [ROOT, ...(process.platform === "linux" ? ["--no-sandbox"] : []), `--user-data-dir=${path.join(home, "userdata")}`],
  env,
});
const page = await app.firstWindow();
await page.setViewportSize({ width: 430, height: 780 });

try {
  // ---- connect ---------------------------------------------------------------------------
  await page.waitForSelector("#setup:not([hidden])", { timeout: 20000 });
  check("a new install asks you to connect first", true);
  await page.fill("#server", API);
  await page.fill("#token", token);
  await page.click("#connect");
  await page.waitForFunction(() => document.getElementById("setup").hidden, null, { timeout: 15000 });
  const status = await page.textContent("#status");
  check("it connects and says what the answers come from", status.includes("Connected") && status.includes("not AI"), status);
  check("the green status dot is on", await page.evaluate(() => document.getElementById("dot").classList.contains("on")));
  check("the footer says these are built-in checks, not an AI model", (await page.textContent("#disclaimer")).includes("not an AI model"));
  check("there is no microphone button and no invented person", !(await page.content()).match(/microphone|whatsapp/i));
  check("the token field is cleared after connecting", (await page.inputValue("#token")) === "");

  // ---- look at a window ------------------------------------------------------------------
  await page.click("#look");
  await page.waitForSelector("#picker:not([hidden])");
  const mine = page.locator("#windows label", { hasText: "KMR Test Window" });
  check("the window picker lists the test window", (await mine.count()) === 1);
  const hiddenNote = await page.textContent("#hidden");
  check("private windows are only counted, not listed", /private window/.test(hiddenNote) || hiddenNote === "", hiddenNote);
  await mine.locator("input").check();
  await page.click("#picker-ok");
  check("a chip shows what it will look at", (await page.textContent("#looking")).includes("KMR Test Window"));

  // ---- preview: exactly what is sent ------------------------------------------------------
  await page.fill("#text", "my page is stuck and shows an error");
  await page.click("#send");
  await page.waitForSelector("#preview:not([hidden])");
  const preview = await page.textContent("#preview-text");
  const parsed = JSON.parse(preview);
  check("a look always shows a preview before anything is sent", parsed.look.windows.length === 1);
  check("only the chosen window is in it", parsed.look.windows[0].title === "KMR Test Window");
  check("personal details were masked before the preview", !preview.includes("jane.doe@example.com") && !preview.includes("9876543210") && preview.includes("[EMAIL]") && preview.includes("[PHONE]"));
  check("no field values are in it (the password field has a name only)", parsed.look.windows[0].nodes.filter((n) => n.role === "text_field").every((n) => n.text === ""));
  check("nothing has been sent yet", (await page.locator(".msg.bot").count()) === 1); // only the greeting
  if (SHOT) await page.screenshot({ path: `${SHOT}/preview.png` });

  // ---- send, then steps ------------------------------------------------------------------
  await page.click("#preview-send");
  try {
    await page.waitForSelector(".step", { timeout: 20000 });
  } catch (error) {
    console.log("LOG:", JSON.stringify(await page.locator(".msg").allTextContents()));
    throw error;
  }
  const reply = await page.locator(".msg.bot").last().textContent();
  check("the reply comes back and says it is not an AI model", reply.includes("I looked at 1 window") && reply.includes("not an AI model"), reply);
  check("the chosen window was cleared: the next message does not look again", (await page.locator("#looking:not([hidden])").count()) === 0);
  const steps = await page.locator(".step").evaluateAll((els) => els.map((e) => ({ kind: e.dataset.kind, title: e.querySelector("h3").textContent })));
  check("it proposes pressing Reload", steps.some((s) => s.kind === "press" && s.title.includes("Reload")), JSON.stringify(steps));
  check("it points at the error text and the unnamed button", steps.filter((s) => s.kind === "highlight").length >= 2);
  check("nothing was pressed just because steps were proposed", gtk.events.length === 0, JSON.stringify(gtk.events));
  if (SHOT) await page.screenshot({ path: `${SHOT}/steps.png` });

  const highlight = page.locator('.step[data-kind="highlight"]').first();
  await highlight.locator("button", { hasText: "Show me where" }).click();
  await settle(600);
  check("'Show me where' describes the element", (await highlight.textContent()).match(/button|label|element/i) !== null);
  check("pointing at something pressed nothing", gtk.events.length === 0);

  // ---- permission: allow one step ---------------------------------------------------------
  const reload = page.locator('.step[data-kind="press"]', { hasText: "Reload" });
  await reload.locator("button", { hasText: "Allow" }).click();
  await reload.locator(".result.ok").waitFor({ timeout: 10000 });
  await settle(1500);
  check("allowing a step presses exactly that one button, once", gtk.events.join() === "EVENT pressed:reload", JSON.stringify(gtk.events));
  check("the step then shows Done and its buttons are gone", (await reload.locator("button").count()) === 0 && (await reload.textContent()).includes("Done"));

  // ---- another question: decline ----------------------------------------------------------
  await page.click("#look");
  await page.waitForSelector("#picker:not([hidden])");
  await page.locator("#windows label", { hasText: "KMR Test Window" }).locator("input").check();
  await page.click("#picker-ok");
  await page.fill("#text", "it is stuck again");
  await page.click("#send");
  await page.waitForSelector("#preview:not([hidden])");
  await page.click("#preview-send");
  await page.waitForFunction(() => document.querySelectorAll('.step[data-kind="press"]').length >= 2, null, { timeout: 20000 });
  const second = page.locator('.step[data-kind="press"]').last();
  gtk.events.length = 0;
  await second.locator("button", { hasText: "Decline" }).click();
  await second.locator(".result.bad").waitFor();
  await settle(800);
  check("declining does nothing to the window", gtk.events.length === 0, JSON.stringify(gtk.events));
  check("a declined step cannot be allowed afterwards", (await second.locator("button").count()) === 0);

  // ---- a message with no look: nothing is read ---------------------------------------------
  await page.click('#lang button[data-lang="hinglish"]');
  check("the window switches to Hinglish", (await page.getAttribute("#text", "placeholder")) === "Problem likhiye…");
  await page.fill("#text", "madad chahiye, page nahi chal raha");
  await page.click("#send");
  await page.waitForFunction(() => document.querySelectorAll(".msg.bot").length >= 4, null, { timeout: 20000 });
  const hinglish = await page.locator(".msg.bot").last().textContent();
  check("a message without a look gets an answer in Hinglish and nothing is read", hinglish.includes("Maine abhi koi window nahi dekhi"), hinglish);
  check("the preview did not appear for a message-only question", await page.evaluate(() => document.getElementById("preview").hidden));
  const usage = await page.textContent("#usage");
  check("the daily usage is shown from the server's count", /\d+ aaj|Aaj \d+/.test(usage) || /\d+\/\d+|\d+ of \d+/.test(usage), usage);

  // ---- theme, settings, staying alive ------------------------------------------------------
  await page.click("#theme");
  check("the theme switches", (await page.getAttribute("html", "data-theme")) === "light");
  await page.click("#gear");
  await page.waitForSelector("#settings:not([hidden])");
  check("settings say the built-in block list always applies", (await page.textContent("#builtin")).match(/never read|kabhi nahi/) !== null);
  await page.click("#settings-close");

  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  await settle(500);
  const visible = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.isVisible()));
  check("closing the window hides it but the assistant keeps running", visible.length === 1 && visible[0] === false, JSON.stringify(visible));
  await app.evaluate(({ app: a }) => a.emit("second-instance"));
  await settle(500);
  check("launching the app again brings the window back", await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()));
  check("the agent kept running while hidden (the status still answers)", (await page.evaluate(() => window.kmr.agent("GET", "/status"))).status === 200);
  check("the window cannot reach the agent outside its own calls", (await page.evaluate(() => window.kmr.agent("POST", "/steps/x/../../config", {}))).status === 400);
  check("the window never receives the agent secret", !JSON.stringify(await page.evaluate(() => Object.keys(window.kmr))).match(/secret|token/i));
} finally {
  await app.close();
  gtk.child.kill();
}

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
