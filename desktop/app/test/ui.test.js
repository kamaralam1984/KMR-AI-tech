import { createRequire } from "node:module";
import { readFileSync, mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { STRINGS } = require("../renderer/strings.js");
const L = require("../renderer/logic.js");
const { allowed } = require("../lib/agent_process.js");
const autostart = require("../lib/autostart.js");

describe("strings", () => {
  it("has the same keys in every language, and no empty text", () => {
    const keys = Object.keys(STRINGS.en).sort();
    for (const lang of ["hinglish", "hi"]) {
      expect(Object.keys(STRINGS[lang]).sort(), lang).toEqual(keys);
    }
    for (const lang of Object.keys(STRINGS)) {
      for (const [k, v] of Object.entries(STRINGS[lang])) {
        expect(v === "" || (Array.isArray(v) && v.length === 0), `${lang}.${k}`).toBe(false);
      }
      expect(STRINGS[lang].chips).toHaveLength(STRINGS[lang].chipMessages.length);
    }
  });

  it("promises nothing the app does not do", () => {
    const text = JSON.stringify(STRINGS.en).toLowerCase();
    expect(text).not.toMatch(/microphone|voice|whatsapp|guarantee|always correct/);
    expect(STRINGS.en.disclaimerAi).toMatch(/can be wrong/);
    expect(STRINGS.en.disclaimerRules).toMatch(/not an AI model/);
  });
});

describe("logic", () => {
  it("shows the connection honestly", () => {
    expect(L.statusLine(null, STRINGS.en)).toBe("Not connected");
    expect(L.statusLine({ connected: false }, STRINGS.en)).toBe("Not connected");
    expect(L.statusLine({ connected: true, provider_label: "Built-in checks (not AI)" }, STRINGS.en)).toBe(
      "Connected · Built-in checks (not AI)",
    );
  });

  it("says plainly whether the answers come from an AI model", () => {
    expect(L.disclaimer({ connected: true, is_ai: false }, STRINGS.en)).toBe(STRINGS.en.disclaimerRules);
    expect(L.disclaimer({ connected: true, is_ai: true }, STRINGS.en)).toBe(STRINGS.en.disclaimerAi);
    expect(L.disclaimer(null, STRINGS.en)).toBe(STRINGS.en.disclaimerAi);
  });

  it("titles steps by what they do to which element", () => {
    expect(L.stepTitle({ kind: "press", name: "Reload", role: "button" }, STRINGS.en)).toBe("Press “Reload”");
    expect(L.stepTitle({ kind: "type", name: "", role: "text_field" }, STRINGS.en)).toBe("Type into (text_field)");
    expect(L.stepTitle({ kind: "highlight", name: "Save", role: "button" }, STRINGS.en)).toContain("Point at");
  });

  it("counts down a step's permission and never goes below zero", () => {
    expect(L.secondsLeft(120000, 0)).toBe(120);
    expect(L.secondsLeft(120000, 119500)).toBe(1);
    expect(L.secondsLeft(120000, 130000)).toBe(0);
    expect(L.clock(119)).toBe("1:59");
    expect(L.clock(5)).toBe("0:05");
  });

  it("describes where an element is without inventing a position", () => {
    expect(L.describeWhere({ role: "button", name: "Reload", bounds: [10, 20, 30, 40] }, "Shop")).toBe(
      "a button named “Reload” in “Shop” around x 10, y 20",
    );
    expect(L.describeWhere({ role: "text_field", name: "", bounds: [0, 0, 5, 5] }, "")).toBe("a text field");
    expect(L.describeWhere({}, "")).toBe("an element");
  });

  it("will not send empty, huge or while busy", () => {
    expect(L.canSend("hi", false)).toBe(true);
    expect(L.canSend("   ", false)).toBe(false);
    expect(L.canSend("x".repeat(2001), false)).toBe(false);
    expect(L.canSend("hi", true)).toBe(false);
  });

  it("fills placeholders", () => {
    expect(L.fill("{n} private window(s)", { n: 3 })).toBe("3 private window(s)");
    expect(L.fill("{x}", {})).toBe("");
  });
});

describe("what the chat window may ask the agent", () => {
  it("allows only the app's own calls", () => {
    const id = "123e4567-e89b-12d3-a456-426614174000";
    for (const [m, p] of [["GET", "/status"], ["GET", "/windows"], ["POST", "/prepare"], ["POST", "/send"], ["POST", "/config"], ["POST", `/steps/${id}/approve`], ["POST", `/steps/${id}/reject`], ["POST", `/steps/${id}/highlight`]]) {
      expect(allowed(m, p), `${m} ${p}`).toBe(true);
    }
    for (const [m, p] of [["GET", "/steps/x/approve"], ["POST", "/steps/../config"], ["POST", "/status"], ["GET", "/prepare"], ["DELETE", "/config"], ["POST", "/steps/abc/approve"], ["POST", `/steps/${id}/approve?x=1`], ["GET", "//evil"]]) {
      expect(allowed(m, p), `${m} ${p}`).toBe(false);
    }
  });
});

describe("start at login", () => {
  it("writes and removes an autostart entry only on request (Linux)", () => {
    if (process.platform !== "linux") return;
    const dir = mkdtempSync(path.join(tmpdir(), "kmr-autostart-"));
    process.env.XDG_CONFIG_HOME = dir;
    const app = { getLoginItemSettings: () => ({ openAtLogin: false }) };
    expect(autostart.isEnabled(app)).toBe(false);
    autostart.setEnabled(app, true, "/opt/kmr/electron", ["/opt/kmr/app"]);
    expect(existsSync(autostart.linuxFile())).toBe(true);
    expect(readFileSync(autostart.linuxFile(), "utf8")).toContain('Exec="/opt/kmr/electron" "/opt/kmr/app"');
    expect(autostart.isEnabled(app)).toBe(true);
    autostart.setEnabled(app, false, "", []);
    expect(autostart.isEnabled(app)).toBe(false);
  });
});

describe("the window is locked down", () => {
  const html = readFileSync(path.join(ROOT, "renderer", "index.html"), "utf8");
  const main = readFileSync(path.join(ROOT, "main.js"), "utf8");
  const app = readFileSync(path.join(ROOT, "renderer", "app.js"), "utf8");
  const preload = readFileSync(path.join(ROOT, "preload.js"), "utf8");

  it("has a strict content policy and no inline script or handler", () => {
    expect(html).toMatch(/default-src 'none'/);
    expect(html).toMatch(/script-src 'self'/);
    expect(html).not.toMatch(/<script>[^<]/);
    expect(html).not.toMatch(/\son[a-z]+=/i);
  });

  it("isolates the page: no node, sandboxed, no navigation, no pop-ups", () => {
    expect(main).toMatch(/contextIsolation: true/);
    expect(main).toMatch(/nodeIntegration: false/);
    expect(main).toMatch(/sandbox: true/);
    expect(main).toMatch(/will-navigate/);
    expect(main).toMatch(/action: "deny"/);
  });

  it("never builds HTML from text and never receives a secret", () => {
    expect(app).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/);
    const code = preload.replace(/\/\/.*$/gm, ""); // comments may mention them; code may not use them
    expect(code).not.toMatch(/secret|token/i);
  });

  it("only a real click counts as permission", () => {
    expect((app.match(/event\.isTrusted/g) || []).length).toBeGreaterThanOrEqual(3);
  });

  it("keeps running when the window closes, unless turned off, and reopens on a second launch", () => {
    expect(main).toMatch(/background !== false/);
    expect(main).toMatch(/second-instance/);
    expect(main).toMatch(/requestSingleInstanceLock/);
  });
});
