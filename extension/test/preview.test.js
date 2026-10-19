import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const SRC = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../src",
);
const load = (name) => {
  const file = path.join(SRC, name);
  delete require.cache[file];
  return require(file);
};

Element.prototype.getBoundingClientRect = function () {
  return {
    left: 10,
    top: 10,
    right: 110,
    bottom: 30,
    width: 100,
    height: 20,
    x: 10,
    y: 10,
  };
};

const PAGE = `
<h1>Shop</h1>
<form id="f"><input id="a" name="a" value="typed by the visitor"><input id="b" name="b"></form>
<img id="hero" src="/a.png">
<button id="go"></button>
<a id="lnk" href="/x"></a>
`;

let K;
function setup(html = PAGE) {
  document.documentElement.removeAttribute("lang");
  document.title = "";
  document.body.innerHTML = html;
  Object.defineProperty(window, "location", {
    value: new URL("https://shop.example.com/support"),
    configurable: true,
    writable: true,
  });
  K = {
    blocklist: load("blocklist.js"),
    redact: load("redact.js"),
    sensitive: load("sensitive.js"),
    collect: load("collect.js"),
    payload: load("payload.js"),
    fixes: load("fixes.js"),
    overlay: load("overlay.js"),
  };
  globalThis.KMR_EXT = K;
}

describe("fixes.js", () => {
  beforeEach(() => setup());

  it("only offers a preview for the checks it can honestly preview", () => {
    for (const check of [
      "images_missing_alt",
      "inputs_without_label",
      "buttons_without_name",
      "links_without_name",
      "missing_lang",
      "missing_title",
    ]) {
      expect(K.fixes.supports(check), check).toBe(true);
    }
    for (const check of [
      "heading_structure",
      "horizontal_overflow",
      "missing_viewport_meta",
      "constructor",
    ]) {
      expect(K.fixes.supports(check), check).toBe(false);
    }
  });

  it("checks what the user wrote: required text, a language code, no markup or control characters", () => {
    expect(K.fixes.validate("images_missing_alt", "").ok).toBe(true); // empty alt = decorative
    expect(K.fixes.validate("inputs_without_label", "  ").ok).toBe(false);
    expect(K.fixes.validate("missing_lang", "english").ok).toBe(false);
    expect(K.fixes.validate("missing_lang", "hi").value).toBe("hi");
    expect(K.fixes.validate("missing_lang", "en-IN").ok).toBe(true);
    expect(
      K.fixes.validate("buttons_without_name", "<b>Buy</b>\u0000 now").value,
    ).toBe("b Buy /b now");
    expect(
      K.fixes.validate("buttons_without_name", "x".repeat(500)).value,
    ).toHaveLength(120);
    expect(K.fixes.validate("nope", "x").ok).toBe(false);
  });

  it("says exactly what it will change", () => {
    expect(K.fixes.describe("inputs_without_label", "Email", "#b")).toBe(
      'Set aria-label="Email" on #b to give this field an accessible name, in this tab only.',
    );
    expect(K.fixes.describe("missing_title", "Shop", "html")).toContain(
      "page title",
    );
  });

  it("applies each fix, checks it again, and undoes it exactly", () => {
    const hero = document.getElementById("hero");
    const alt = K.fixes.apply(
      document,
      hero,
      "images_missing_alt",
      "A red shoe",
    );
    expect(hero.getAttribute("alt")).toBe("A red shoe");
    expect(alt.verified).toBe(true);
    alt.undo();
    expect(hero.hasAttribute("alt")).toBe(false);

    const go = document.getElementById("go");
    go.setAttribute("aria-label", "old");
    const label = K.fixes.apply(document, go, "buttons_without_name", "Buy");
    expect(go.getAttribute("aria-label")).toBe("Buy");
    label.undo();
    expect(go.getAttribute("aria-label")).toBe("old"); // restored, not removed

    const lang = K.fixes.apply(
      document,
      document.documentElement,
      "missing_lang",
      "en",
    );
    expect(document.documentElement.getAttribute("lang")).toBe("en");
    lang.undo();
    expect(document.documentElement.hasAttribute("lang")).toBe(false);

    const title = K.fixes.apply(
      document,
      document.documentElement,
      "missing_title",
      "Shop",
    );
    expect(document.title).toBe("Shop");
    title.undo();
    expect(document.title).toBe("");
  });

  it("never touches a field's value, and an empty alt counts as a valid fix", () => {
    const a = document.getElementById("a");
    K.fixes.apply(document, a, "inputs_without_label", "Name");
    expect(a.value).toBe("typed by the visitor");
    const hero = document.getElementById("hero");
    expect(
      K.fixes.apply(document, hero, "images_missing_alt", "").verified,
    ).toBe(true);
    expect(hero.getAttribute("alt")).toBe("");
  });
});

// ------------------------------------------------------------------------------- the flow

const FINDINGS = {
  provider_label: "Built-in rules (not AI)",
  is_ai: false,
  notice: "",
  findings: [
    {
      title: "Images without alternative text",
      severity: "low",
      confidence: "high",
      classification: "diagnosed",
      observed: ["On /: 1 occurrence(s)."],
      inferred: [],
      unverified: [],
      selectors: ["#hero"],
      fix_targets: [{ selector: "#hero", check: "images_missing_alt" }],
      web_path: "/issues/abc",
    },
    {
      title: "Form fields without a label",
      severity: "medium",
      confidence: "high",
      classification: "diagnosed",
      observed: [],
      inferred: [],
      unverified: [],
      selectors: ["#a", "#b"],
      fix_targets: [
        { selector: "#a", check: "inputs_without_label" },
        { selector: "#b", check: "inputs_without_label" },
      ],
      web_path: "/issues/def",
    },
    {
      title: "Page does not have exactly one main heading",
      severity: "info",
      confidence: "high",
      classification: "diagnosed",
      observed: [],
      inferred: [],
      unverified: [],
      selectors: ["h1"],
      fix_targets: [{ selector: "h1", check: "heading_structure" }],
      web_path: "/issues/ghi",
    },
  ],
};

function startContent() {
  delete window.__kmrAgentContent;
  const sent = [];
  let listener = null;
  globalThis.chrome = {
    runtime: {
      id: "test",
      sendMessage: vi.fn(async (message) => {
        sent.push(message);
        return message.type === "kmr:send"
          ? { ok: true, result: FINDINGS }
          : { ok: true };
      }),
      onMessage: {
        addListener: (fn) => (listener = fn),
        removeListener: () => (listener = null),
      },
    },
  };
  load("content.js");
  const call = (message) =>
    new Promise((resolve) => listener(message, {}, resolve));
  return { sent, call };
}

const shadow = () => document.querySelector("kmr-ai-agent").shadowRoot;
const button = (text) =>
  [...shadow().querySelectorAll("button")].find((b) => b.textContent === text);
const panelText = () => shadow().querySelector(".panel").textContent;
const type = (value) => {
  shadow().querySelector("input").value = value; // the user typing in the agent's own panel
};
const fresh = async () => {
  setup();
  const bridge = startContent();
  await bridge.call({
    type: "kmr:configure",
    settings: { confirmBeforeSend: false, testMode: true, glideMs: 0 },
  });
  await bridge.call({ type: "kmr:analyze", projectId: "p" });
  return bridge;
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

afterEach(() => {
  document.querySelectorAll("kmr-ai-agent").forEach((n) => n.remove());
  delete window.__kmrAgentContent;
});

describe("Preview fix flow", () => {
  it("offers Preview fix only for findings it can preview", async () => {
    await fresh();
    const cards = [...shadow().querySelectorAll(".finding")];
    expect(cards).toHaveLength(3);
    const has = (card) =>
      [...card.querySelectorAll("button")].some(
        (b) => b.textContent === "Preview fix",
      );
    expect(cards.map(has)).toEqual([true, true, false]);
    expect(panelText()).toContain("changes only this tab's copy");
  });

  it("changes nothing until the user allows, and says exactly what it will do", async () => {
    await fresh();
    button("Preview fix").click();
    const hero = document.getElementById("hero");
    expect(panelText()).toContain("Allow KMR AI to change this element?");
    expect(panelText()).toContain("#hero");
    expect(panelText()).toContain("in this tab only");
    expect(hero.hasAttribute("alt")).toBe(false);
    expect(shadow().querySelector(".cursor").dataset.target).toBe("#hero"); // pointer already there
    button("Cancel").click();
    expect(hero.hasAttribute("alt")).toBe(false);
    expect(panelText()).toContain("Preview fix"); // back at the findings
  });

  it("refuses an empty required answer and still changes nothing", async () => {
    await fresh();
    button("Preview fix").click(); // image: empty alt is allowed, so use the form-field finding
    button("Cancel").click();
    [...shadow().querySelectorAll("button")]
      .filter((b) => b.textContent === "Preview fix")[1]
      .click();
    type("   ");
    button("Allow").click();
    await settle();
    expect(shadow().querySelector(".err").textContent).toBe(
      "Please write something.",
    );
    expect(document.getElementById("a").hasAttribute("aria-label")).toBe(false);
  });

  it("moves the pointer, then applies the user's own text, marks it done and offers Undo", async () => {
    await fresh();
    const events = [];
    for (const t of [
      "click",
      "input",
      "change",
      "submit",
      "keydown",
      "focusin",
      "mousedown",
    ]) {
      document.addEventListener(
        t,
        (e) => {
          if (
            !e.composedPath().includes(document.querySelector("kmr-ai-agent"))
          )
            events.push(t);
        },
        true,
      );
    }
    const valueBefore = document.getElementById("a").value;
    button("Preview fix").click();
    type("A red running shoe");
    button("Allow").click();
    await settle();
    const hero = document.getElementById("hero");
    expect(hero.getAttribute("alt")).toBe("A red running shoe");
    expect(shadow().querySelector(".cursor").dataset.target).toBe("#hero");
    expect(shadow().querySelector(".box.done")).not.toBeNull();
    expect(panelText()).toContain("Done (preview)");
    expect(panelText()).toContain("Your real website is unchanged");
    expect(events).toEqual([]); // nothing the page could see as a click, key or input
    expect(document.getElementById("a").value).toBe(valueBefore);

    button("Undo").click();
    expect(hero.hasAttribute("alt")).toBe(false);
    expect(panelText()).toContain("Undone");
  });

  it("walks through several elements, one permission each", async () => {
    await fresh();
    [...shadow().querySelectorAll("button")]
      .filter((b) => b.textContent === "Preview fix")[1]
      .click();
    expect(panelText()).toContain("(1 of 2)");
    type("Your name");
    button("Allow").click();
    await settle();
    expect(document.getElementById("a").getAttribute("aria-label")).toBe(
      "Your name",
    );
    expect(document.getElementById("b").hasAttribute("aria-label")).toBe(false); // not yet asked
    button("Next element").click();
    expect(panelText()).toContain("(2 of 2)");
    type("Your email");
    button("Allow").click();
    await settle();
    expect(document.getElementById("b").getAttribute("aria-label")).toBe(
      "Your email",
    );
    expect(button("Next element")).toBeUndefined();
  });

  it("turning the agent off puts the page back exactly as it was", async () => {
    const bridge = await fresh();
    button("Preview fix").click();
    type("A shoe");
    button("Allow").click();
    await settle();
    expect(document.getElementById("hero").getAttribute("alt")).toBe("A shoe");
    await bridge.call({ type: "kmr:off" });
    expect(document.getElementById("hero").hasAttribute("alt")).toBe(false);
    expect(document.querySelector("kmr-ai-agent")).toBeNull();
  });

  it("will not apply anything if the page has become sensitive in the meantime", async () => {
    await fresh();
    button("Preview fix").click();
    type("A shoe");
    document.body.insertAdjacentHTML("beforeend", '<input type="password">');
    button("Allow").click();
    await settle();
    expect(document.getElementById("hero").hasAttribute("alt")).toBe(false);
    expect(panelText()).toContain("Not reading this page");
  });

  it("ignores a click that a page script fabricates (production: events must be trusted)", () => {
    setup();
    const ui = K.overlay.createOverlay(document, window, { mode: "open" }); // strict: no trustSynthetic
    ui.mount();
    const allowed = vi.fn();
    ui.askPermission({
      heading: "h",
      lines: [],
      field: { prompt: "p" },
      onAllow: allowed,
      onCancel: vi.fn(),
    });
    const allow = [...ui.root.querySelectorAll("button")].find(
      (b) => b.textContent === "Allow",
    );
    allow.click();
    allow.dispatchEvent(
      new MouseEvent("click", { bubbles: true, composed: true }),
    );
    expect(allowed).not.toHaveBeenCalled();
    const lenient = K.overlay.createOverlay(document, window, {
      mode: "open",
      trustSynthetic: true,
    });
    lenient.mount();
    lenient.askPermission({
      heading: "h",
      lines: [],
      field: { prompt: "p" },
      onAllow: allowed,
      onCancel: vi.fn(),
    });
    [...lenient.root.querySelectorAll("button")]
      .find((b) => b.textContent === "Allow")
      .click();
    expect(allowed).toHaveBeenCalledTimes(1); // only the test switch lets it through
  });

  it("keeps the production overlay closed and the test switch out of what the background sends", () => {
    const background = readFileSync(path.join(SRC, "background.js"), "utf8");
    expect(background).not.toMatch(/testMode|trustSynthetic/);
    const content = readFileSync(path.join(SRC, "content.js"), "utf8");
    expect(content).toMatch(
      /settings\.testMode \? \{ mode: "open", trustSynthetic: true \} : \{\}/,
    );
  });
});
