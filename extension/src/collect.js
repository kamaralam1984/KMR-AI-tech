/*
 * Collects what the agent may look at. This is an ALLOW-LIST:
 *   - never the value of any input, textarea, select or editable element;
 *   - never cookies, storage, headers or request/response bodies;
 *   - text only from titles, headings, alerts and (as problems) unnamed controls, redacted.
 * Elements are described by a position-only CSS path (tag and nth-of-type, or a short plain id),
 * never by classes or attribute values, so a selector cannot carry personal data.
 */
"use strict";

(function () {

const dep = (name, path) =>
  typeof require === "function" ? require(path) : globalThis.KMR_EXT[name];
const redact = dep("redact", "./redact.js");

const MAX_NODES = 3000;
const MAX_SELECTORS = 5;
const ID_OK = /^[A-Za-z][A-Za-z0-9_-]{0,30}$/;

function isVisible(el, win) {
  if (!el || el.nodeType !== 1) return false;
  const style = win.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden") return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 || rect.height > 0 || el.tagName === "BODY";
}

function cssPath(el, doc) {
  if (el.id && ID_OK.test(el.id) && !/\d{4,}/.test(el.id)) {
    const same = doc.querySelectorAll(`#${CSS.escape(el.id)}`);
    if (same.length === 1) return `#${el.id}`;
  }
  const parts = [];
  let node = el;
  while (node && node.nodeType === 1 && node !== doc.documentElement && parts.length < 8) {
    const tag = node.tagName.toLowerCase();
    if (tag === "body") {
      parts.unshift("body");
      break;
    }
    const siblings = [...node.parentElement.children].filter((c) => c.tagName === node.tagName);
    parts.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${siblings.indexOf(node) + 1})` : tag);
    node = node.parentElement;
  }
  return parts.join(" > ");
}

function hasName(el) {
  return !!(
    (el.getAttribute("aria-label") || "").trim() ||
    el.getAttribute("aria-labelledby") ||
    (el.getAttribute("title") || "").trim() ||
    (el.textContent || "").trim() ||
    el.querySelector("img[alt]:not([alt=''])")
  );
}

function hasLabel(el, doc) {
  if (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby")) return true;
  if (el.getAttribute("title")) return true;
  if (el.id && doc.querySelector(`label[for="${CSS.escape(el.id)}"]`)) return true;
  return !!el.closest("label");
}

function textOf(el, counts, limit = 120) {
  return redact.redactText(el.textContent || "", counts, limit);
}

/**
 * @returns {{observations: object[], counts: object, maskRects: object[], screenshotSafe: boolean}}
 */
function collectPage(doc, win, runtime = {}) {
  const counts = redact.newCounts();
  const pageUrl = redact.redactUrl(win.location.href, counts);
  const observations = [];
  const add = (kind, data) => observations.push({ kind, page_url: pageUrl, data });

  // ---- page summary (allow-listed text only) -----------------------------------------
  const headings = [...doc.querySelectorAll("h1, h2, h3")]
    .filter((h) => isVisible(h, win))
    .slice(0, 8)
    .map((h) => textOf(h, counts, 100))
    .filter(Boolean);
  const alerts = [
    ...doc.querySelectorAll('[role="alert"], [aria-live="assertive"], .error, .alert, .errors'),
  ]
    .filter((a) => isVisible(a, win))
    .slice(0, 5)
    .map((a) => textOf(a, counts, 160))
    .filter(Boolean);
  const nav = win.performance && win.performance.getEntriesByType("navigation")[0];
  const viewportWidth = Math.min(win.innerWidth, win.screen ? win.screen.width : win.innerWidth);
  add("page", {
    title: redact.redactText(doc.title || "", counts, 200),
    lang: redact.redactText(doc.documentElement.getAttribute("lang") || "", counts, 20),
    h1_count: doc.querySelectorAll("h1").length,
    headings,
    alerts,
    load_ms: nav && nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
    viewport_width: viewportWidth,
  });

  // ---- structural checks, each with where to look ------------------------------------
  const check = (name, elements, detail) => {
    if (!elements.length) return;
    const data = {
      check: name,
      count: elements.length,
      selectors: elements.slice(0, MAX_SELECTORS).map((e) => cssPath(e, doc)),
    };
    if (detail) data.detail = detail;
    add("a11y_issue", data);
  };
  const vis = (list) => [...list].filter((e) => isVisible(e, win));
  check(
    "images_missing_alt",
    vis(doc.images).filter((i) => !i.hasAttribute("alt")),
  );
  check(
    "inputs_without_label",
    vis(
      doc.querySelectorAll(
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]), select, textarea",
      ),
    ).filter((e) => !hasLabel(e, doc)),
  );
  check(
    "buttons_without_name",
    vis(doc.querySelectorAll("button, [role=button]")).filter((e) => !hasName(e)),
  );
  check(
    "links_without_name",
    vis(doc.querySelectorAll("a[href]")).filter((e) => !hasName(e)),
  );
  if (!doc.documentElement.getAttribute("lang")) check("missing_lang", [doc.documentElement]);
  if (!doc.title) check("missing_title", [doc.documentElement]);
  if (!doc.querySelector("meta[name=viewport]")) {
    check("missing_viewport_meta", [doc.documentElement]);
  }
  const h1s = doc.querySelectorAll("h1");
  if (h1s.length !== 1) {
    check("heading_structure", h1s.length ? [...h1s] : [doc.body], "expected exactly one h1");
  }
  const wide = doc.documentElement.scrollWidth > viewportWidth + 1;
  if (wide) {
    check("horizontal_overflow", [doc.body], `wider than the ${viewportWidth}px screen`);
  }

  // ---- what happened after the agent was turned on ----------------------------------
  for (const entry of (runtime.console || []).slice(0, 20)) {
    add("console_error", {
      message: redact.redactText(entry.message, counts, 300),
      level: entry.level === "warn" ? "warn" : "error",
    });
  }
  for (const entry of (runtime.pageErrors || []).slice(0, 20)) {
    add("page_error", { message: redact.redactText(entry.message, counts, 300) });
  }
  for (const entry of (runtime.failed || []).slice(0, 20)) {
    add("failed_request", {
      url: redact.redactUrl(entry.url, counts),
      failure: redact.redactText(entry.failure || "network error", counts, 100),
      method: String(entry.method || "GET").slice(0, 10),
    });
  }
  const seen = new Set();
  const httpErrors = [...(runtime.http || [])];
  for (const r of (win.performance && win.performance.getEntriesByType("resource")) || []) {
    if (r.responseStatus >= 400) {
      httpErrors.push({ url: r.name, status: r.responseStatus, resource_type: r.initiatorType });
    }
  }
  for (const entry of httpErrors) {
    const url = redact.redactUrl(entry.url, counts);
    const key = `${url}|${entry.status}`;
    if (seen.has(key) || seen.size >= 20) continue;
    seen.add(key);
    add("http_error", {
      url,
      status: Number(entry.status) || 0,
      resource_type: String(entry.resource_type || "other").slice(0, 20),
    });
  }

  // ---- regions to black out of a screenshot -----------------------------------------
  const maskRects = [];
  const mask = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      maskRects.push({ x: r.left, y: r.top, w: r.width, h: r.height });
    }
  };
  doc.querySelectorAll("input, textarea, select, [contenteditable]").forEach(mask);
  const walker = doc.createTreeWalker(doc.body, 4 /* NodeFilter.SHOW_TEXT */);
  let n = 0;
  for (let node = walker.nextNode(); node && n < MAX_NODES; node = walker.nextNode(), n += 1) {
    const raw = node.data || "";
    if (raw.trim().length < 5) continue;
    const scratch = redact.newCounts();
    redact.redactText(raw, scratch, 2000);
    if (Object.keys(scratch).length && node.parentElement) mask(node.parentElement);
  }
  const screenshotSafe = !doc.querySelector("iframe, video, canvas, embed, object");

  return { observations, counts, maskRects, screenshotSafe };
}

const api = { collectPage, cssPath, isVisible };
if (typeof module !== "undefined" && module.exports) module.exports = api;
else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).collect = api;
})();
