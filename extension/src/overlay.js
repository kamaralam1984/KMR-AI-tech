/*
 * The on-page overlay: highlight boxes, a separate visible agent cursor, and a small panel.
 *
 * Everything lives in one host element with a closed shadow root, so page scripts cannot read or
 * restyle it. The host and all overlay layers use pointer-events: none, except the panel's own
 * buttons. The cursor is only a picture drawn by this extension: it never clicks, types or
 * dispatches input events. Highlighting only reads element positions.
 */
"use strict";

(function () {

const STYLE = `
:host { all: initial; }
.layer { position: fixed; inset: 0; pointer-events: none; z-index: 2147483646; font: 13px/1.4 system-ui, sans-serif; }
.box { position: fixed; border: 2px solid #D64545; border-radius: 4px; background: rgba(214,69,69,.08); box-sizing: border-box; }
.box .chip { position: absolute; left: -2px; top: -24px; max-width: 280px; padding: 2px 8px; border-radius: 4px;
  background: #D64545; color: #fff; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cursor { position: fixed; left: 0; top: 0; width: 22px; height: 22px; transition: transform .6s cubic-bezier(.2,.8,.2,1); }
.cursor .tag { position: absolute; left: 20px; top: 14px; padding: 1px 6px; border-radius: 4px; background: #4568F5; color: #fff; font-weight: 600; white-space: nowrap; }
@media (prefers-reduced-motion: reduce) { .cursor { transition: none; } }
.panel { position: fixed; right: 16px; bottom: 16px; width: min(360px, calc(100vw - 32px)); max-height: 70vh; overflow: auto;
  pointer-events: auto; background: #fff; color: #172033; border: 1px solid #E4E8F0; border-radius: 8px; box-shadow: 0 8px 24px rgba(11,18,32,.2); }
.panel header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; background: #0B1220; color: #fff; border-radius: 8px 8px 0 0; font-weight: 600; }
.panel .body { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.panel button { font: inherit; cursor: pointer; min-height: 32px; padding: 4px 10px; border-radius: 6px; border: 1px solid #E4E8F0; background: #fff; color: #172033; }
.panel button.primary { background: #4568F5; border-color: #4568F5; color: #fff; }
.panel button:focus-visible { outline: 3px solid #4568F5; outline-offset: 2px; }
.panel pre { margin: 0; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #F5F7FB; padding: 8px; border-radius: 6px; font-size: 12px; }
.finding { border: 1px solid #E4E8F0; border-radius: 6px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
.sev { font-weight: 700; }
.muted { color: #667085; font-size: 12px; }
.row { display: flex; flex-wrap: wrap; gap: 6px; }
.warn { background: #FFF6E5; border: 1px solid #C98212; padding: 8px; border-radius: 6px; }
`;

const SVG_NS = "http://www.w3.org/2000/svg";

function el(doc, tag, props = {}, children = []) {
  const node = doc.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function createOverlay(doc, win, { mode = "closed" } = {}) {
  const host = doc.createElement("kmr-ai-agent");
  host.setAttribute("data-kmr-agent", "overlay");
  const root = host.attachShadow({ mode });
  root.append(el(doc, "style", { text: STYLE }));

  const layer = el(doc, "div", { class: "layer" });
  const boxes = el(doc, "div");
  const cursor = el(doc, "div", { class: "cursor", "aria-hidden": "true" });
  const arrow = doc.createElementNS(SVG_NS, "svg");
  arrow.setAttribute("viewBox", "0 0 22 22");
  arrow.setAttribute("width", "22");
  arrow.setAttribute("height", "22");
  const path = doc.createElementNS(SVG_NS, "path");
  path.setAttribute("d", "M3 2 L3 17 L7.5 13 L10.5 19.5 L13 18.3 L10 12 L16 12 Z");
  path.setAttribute("fill", "#4568F5");
  path.setAttribute("stroke", "#fff");
  path.setAttribute("stroke-width", "1.5");
  arrow.append(path);
  cursor.append(arrow, el(doc, "span", { class: "tag", text: "KMR AI" }));
  cursor.style.transform = "translate(-100px,-100px)";
  layer.append(boxes, cursor);

  const panel = el(doc, "section", {
    class: "panel",
    role: "region",
    "aria-label": "KMR AI page agent",
  });
  const status = el(doc, "div", { role: "status", "aria-live": "polite" });
  const content = el(doc, "div", { class: "body" });
  const closeButton = el(doc, "button", { type: "button", "aria-label": "Turn the agent off" });
  closeButton.textContent = "Off";
  const header = el(doc, "header", {}, [el(doc, "span", { text: "KMR AI page agent" }), closeButton]);
  panel.append(header, el(doc, "div", { class: "body" }, [status]), content);
  root.append(layer, panel);

  let tracked = []; // { selector, label, box }
  let raf = 0;

  function find(selector) {
    try {
      return doc.querySelector(selector);
    } catch {
      return null;
    }
  }
  function place(box, target) {
    const r = target.getBoundingClientRect();
    Object.assign(box.style, {
      left: `${r.left}px`,
      top: `${r.top}px`,
      width: `${r.width}px`,
      height: `${r.height}px`,
    });
    return r;
  }
  function reposition() {
    raf = 0;
    for (const item of tracked) {
      const target = find(item.selector);
      if (target) place(item.box, target);
    }
  }
  function schedule() {
    if (!raf) raf = win.requestAnimationFrame(reposition);
  }
  win.addEventListener("scroll", schedule, true);
  win.addEventListener("resize", schedule);

  const api = {
    host,
    root,
    onOff: (fn) => closeButton.addEventListener("click", fn),
    mount() {
      if (!host.isConnected) doc.documentElement.append(host);
    },
    setStatus(text, tone = "info") {
      status.textContent = text;
      status.style.color = tone === "danger" ? "#D64545" : "#172033";
    },
    clearContent() {
      content.replaceChildren();
    },
    showBlocked(reason) {
      api.clearContent();
      api.setStatus("Not reading this page.");
      content.append(
        el(doc, "div", { class: "warn" }, [
          el(doc, "strong", { text: "Not reading this page. " }),
          el(doc, "span", { text: `Nothing was collected or sent: ${reason}.` }),
        ]),
      );
    },
    showPreview(text, { onSend, onCancel }) {
      api.clearContent();
      api.setStatus("This is exactly what will be sent. Nothing has been sent yet.");
      const send = el(doc, "button", { type: "button", class: "primary", text: "Send for analysis" });
      send.addEventListener("click", onSend);
      const cancel = el(doc, "button", { type: "button", text: "Cancel" });
      cancel.addEventListener("click", onCancel);
      content.append(el(doc, "pre", { text }), el(doc, "div", { class: "row" }, [send, cancel]));
    },
    highlight(selector, label) {
      const target = find(selector);
      if (!target) return null;
      const box = el(doc, "div", { class: "box" }, [el(doc, "span", { class: "chip", text: label })]);
      boxes.append(box);
      tracked.push({ selector, label, box });
      place(box, target);
      return box;
    },
    clearHighlights() {
      boxes.replaceChildren();
      tracked = [];
    },
    /** Moves the visible agent cursor next to the element. It does not click or focus it. */
    moveCursorTo(selector) {
      const target = find(selector);
      if (!target) return false;
      if (typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ block: "center", behavior: "auto" });
      }
      const r = target.getBoundingClientRect();
      cursor.style.transform = `translate(${Math.round(r.left + r.width / 2)}px,${Math.round(r.top + r.height / 2)}px)`;
      cursor.dataset.target = selector;
      return true;
    },
    showFindings(result, { onFix, onDismiss }) {
      api.clearContent();
      api.clearHighlights();
      api.setStatus(
        result.findings.length
          ? `${result.findings.length} finding(s). ${result.provider_label}. Nothing was changed.`
          : "No findings. Nothing was changed.",
      );
      result.findings.forEach((f, index) => {
        const label = `${index + 1}. ${f.title}`;
        f.selectors.forEach((sel) => api.highlight(sel, label));
        const show = el(doc, "button", { type: "button", text: "Show on page" });
        show.addEventListener("click", () => {
          const ok = f.selectors.some((sel) => api.moveCursorTo(sel));
          api.setStatus(
            ok ? `Showing: ${f.title}` : "Could not find that element on the page right now.",
            ok ? "info" : "danger",
          );
        });
        const fix = el(doc, "button", { type: "button", class: "primary", text: "Fix this in KMR AI" });
        fix.addEventListener("click", () => onFix(f));
        const dismiss = el(doc, "button", { type: "button", text: "Dismiss" });
        dismiss.addEventListener("click", () => onDismiss(f, card));
        const card = el(doc, "article", { class: "finding" }, [
          el(doc, "div", {}, [
            el(doc, "span", { class: "sev", text: `${f.severity.toUpperCase()} · ` }),
            el(doc, "span", { text: f.title }),
          ]),
          el(doc, "div", { class: "muted", text: `Confidence: ${f.confidence}${f.classification === "needs_review" ? " · Needs review" : ""}` }),
          ...f.observed.slice(0, 2).map((t) => el(doc, "div", { text: `Observed: ${t}` })),
          ...f.inferred.slice(0, 1).map((t) => el(doc, "div", { class: "muted", text: `Inferred (not proven): ${t}` })),
          ...f.unverified.slice(0, 1).map((t) => el(doc, "div", { class: "muted", text: `Unverified: ${t}` })),
          el(doc, "div", { class: "row" }, [show, fix, dismiss]),
        ]);
        content.append(card);
      });
      content.append(
        el(doc, "div", { class: "muted", text: "“Fix this” opens KMR AI, where you review the exact change and approve it. This extension changes nothing." }),
      );
    },
    destroy() {
      win.removeEventListener("scroll", schedule, true);
      win.removeEventListener("resize", schedule);
      if (raf) win.cancelAnimationFrame(raf);
      host.remove();
    },
  };
  return api;
}

const api = { createOverlay };
if (typeof module !== "undefined" && module.exports) module.exports = api;
else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).overlay = api;
})();
