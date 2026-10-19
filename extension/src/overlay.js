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
.ask { border: 2px solid #4568F5; border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 8px; background: #F5F7FF; }
.ask input { font: inherit; padding: 6px 8px; border: 1px solid #667085; border-radius: 6px; color: #172033; background: #fff; }
.err { color: #D64545; font-weight: 600; }
.ok { color: #168A62; font-weight: 600; }
.box.done { border-color: #168A62; background: rgba(22,138,98,.10); }
.box.done .chip { background: #168A62; }
.ring { position: absolute; left: 3px; top: 2px; width: 14px; height: 14px; border-radius: 50%; border: 3px solid #4568F5; opacity: 0; }
.press .ring { animation: press .55s ease-out; }
@keyframes press { 0% { opacity: .95; transform: scale(.6); } 100% { opacity: 0; transform: scale(3.2); } }
@media (prefers-reduced-motion: reduce) { .press .ring { animation: none; } }
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

  function createOverlay(
    doc,
    win,
    { mode = "closed", trustSynthetic = false } = {},
  ) {
    // A permission only counts if it comes from a real user click. `trustSynthetic` exists so jsdom
    // tests (whose events are never trusted) can drive the flow; production never sets it.
    const real = (event) => event.isTrusted || trustSynthetic;
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
    path.setAttribute(
      "d",
      "M3 2 L3 17 L7.5 13 L10.5 19.5 L13 18.3 L10 12 L16 12 Z",
    );
    path.setAttribute("fill", "#4568F5");
    path.setAttribute("stroke", "#fff");
    path.setAttribute("stroke-width", "1.5");
    arrow.append(path);
    cursor.append(
      el(doc, "span", { class: "ring" }),
      arrow,
      el(doc, "span", { class: "tag", text: "KMR AI" }),
    );
    cursor.style.transform = "translate(-100px,-100px)";
    layer.append(boxes, cursor);

    const panel = el(doc, "section", {
      class: "panel",
      role: "region",
      "aria-label": "KMR AI page agent",
    });
    const status = el(doc, "div", { role: "status", "aria-live": "polite" });
    const content = el(doc, "div", { class: "body" });
    const closeButton = el(doc, "button", {
      type: "button",
      "aria-label": "Turn the agent off",
    });
    closeButton.textContent = "Off";
    const header = el(doc, "header", {}, [
      el(doc, "span", { text: "KMR AI page agent" }),
      closeButton,
    ]);
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
            el(doc, "span", {
              text: `Nothing was collected or sent: ${reason}.`,
            }),
          ]),
        );
      },
      showPreview(text, { onSend, onCancel }) {
        api.clearContent();
        api.setStatus(
          "This is exactly what will be sent. Nothing has been sent yet.",
        );
        const send = el(doc, "button", {
          type: "button",
          class: "primary",
          text: "Send for analysis",
        });
        send.addEventListener("click", onSend);
        const cancel = el(doc, "button", { type: "button", text: "Cancel" });
        cancel.addEventListener("click", onCancel);
        content.append(
          el(doc, "pre", { text }),
          el(doc, "div", { class: "row" }, [send, cancel]),
        );
      },
      highlight(selector, label) {
        const target = find(selector);
        if (!target) return null;
        const box = el(doc, "div", { class: "box" }, [
          el(doc, "span", { class: "chip", text: label }),
        ]);
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
      showFindings(result, { onFix, onDismiss, onPreview, canPreview }) {
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
          const show = el(doc, "button", {
            type: "button",
            text: "Show on page",
          });
          show.addEventListener("click", () => {
            const ok = f.selectors.some((sel) => api.moveCursorTo(sel));
            api.setStatus(
              ok
                ? `Showing: ${f.title}`
                : "Could not find that element on the page right now.",
              ok ? "info" : "danger",
            );
          });
          const fix = el(doc, "button", {
            type: "button",
            class: "primary",
            text: "Fix this in KMR AI",
          });
          fix.addEventListener("click", () => onFix(f));
          const dismiss = el(doc, "button", {
            type: "button",
            text: "Dismiss",
          });
          dismiss.addEventListener("click", () => onDismiss(f, card));
          const preview = el(doc, "button", {
            type: "button",
            text: "Preview fix",
          });
          preview.addEventListener("click", (event) => {
            if (real(event)) onPreview(f);
          });
          const canTry = onPreview && canPreview && canPreview(f);
          const card = el(doc, "article", { class: "finding" }, [
            el(doc, "div", {}, [
              el(doc, "span", {
                class: "sev",
                text: `${f.severity.toUpperCase()} · `,
              }),
              el(doc, "span", { text: f.title }),
            ]),
            el(doc, "div", {
              class: "muted",
              text: `Confidence: ${f.confidence}${f.classification === "needs_review" ? " · Needs review" : ""}`,
            }),
            ...f.observed
              .slice(0, 2)
              .map((t) => el(doc, "div", { text: `Observed: ${t}` })),
            ...f.inferred
              .slice(0, 1)
              .map((t) =>
                el(doc, "div", {
                  class: "muted",
                  text: `Inferred (not proven): ${t}`,
                }),
              ),
            ...f.unverified
              .slice(0, 1)
              .map((t) =>
                el(doc, "div", { class: "muted", text: `Unverified: ${t}` }),
              ),
            el(
              doc,
              "div",
              { class: "row" },
              canTry ? [show, preview, fix, dismiss] : [show, fix, dismiss],
            ),
          ]);
          content.append(card);
        });
        content.append(
          el(doc, "div", {
            class: "muted",
            text: "“Preview fix” changes only this tab's copy of the page, one element at a time, after you allow it. Reloading undoes it and your real website is unchanged. “Fix this in KMR AI” opens the reviewed fix flow, where you approve the exact change.",
          }),
        );
      },
      /** The visible "press": a ring on the agent cursor. It is a drawing only; nothing is clicked. */
      pulseCursor() {
        cursor.classList.remove("press");
        void cursor.offsetWidth; // restart the animation
        cursor.classList.add("press");
      },
      markFixed(selector) {
        for (const item of tracked)
          if (item.selector === selector) item.box.classList.add("done");
      },
      /**
       * Asks permission for ONE change. Nothing happens until the user presses Allow with a real
       * click; a click that a page script fabricates (isTrusted false) is ignored.
       */
      askPermission({ heading, lines, field, onAllow, onCancel }) {
        api.clearContent();
        api.setStatus("Waiting for your permission. Nothing has been changed.");
        const input = field
          ? el(doc, "input", {
              type: "text",
              maxlength: "120",
              "aria-label": field.prompt,
            })
          : null;
        const error = el(doc, "div", { class: "err", role: "alert" });
        const allow = el(doc, "button", {
          type: "button",
          class: "primary",
          text: "Allow",
        });
        const cancel = el(doc, "button", { type: "button", text: "Cancel" });
        allow.addEventListener("click", (event) => {
          if (!real(event)) return;
          error.textContent = "";
          onAllow(input ? input.value : "", (message) => {
            error.textContent = message;
          });
        });
        cancel.addEventListener("click", (event) => {
          if (real(event)) onCancel();
        });
        if (input) {
          // Keep what the user types out of the page's key listeners where it can be.
          for (const type of ["keydown", "keyup", "keypress", "input"]) {
            input.addEventListener(type, (event) => event.stopPropagation());
          }
        }
        content.append(
          el(
            doc,
            "div",
            { class: "ask", role: "group", "aria-label": "Permission" },
            [
              el(doc, "strong", { text: heading }),
              ...lines.map((t) => el(doc, "div", { text: t })),
              ...(input
                ? [
                    el(doc, "label", { class: "muted" }, [
                      el(doc, "span", { text: `${field.prompt}: ` }),
                    ]),
                    input,
                    el(doc, "div", {
                      class: "muted",
                      text: "Write a short description. Do not type personal details.",
                    }),
                  ]
                : []),
              error,
              el(doc, "div", { class: "row" }, [allow, cancel]),
            ],
          ),
        );
      },
      showApplied({ lines, verified, onUndo, onNext, onDone }) {
        api.clearContent();
        api.setStatus("Preview applied in this tab only.");
        const undo = el(doc, "button", { type: "button", text: "Undo" });
        undo.addEventListener("click", (event) => {
          if (real(event)) onUndo();
        });
        const buttons = [undo];
        if (onNext) {
          const next = el(doc, "button", {
            type: "button",
            class: "primary",
            text: "Next element",
          });
          next.addEventListener("click", (event) => {
            if (real(event)) onNext();
          });
          buttons.unshift(next);
        }
        const done = el(doc, "button", {
          type: "button",
          text: "Back to findings",
        });
        done.addEventListener("click", (event) => {
          if (real(event)) onDone();
        });
        buttons.push(done);
        content.append(
          el(doc, "div", { class: "ask" }, [
            el(doc, "div", {
              class: verified ? "ok" : "err",
              text: verified ? "Done (preview)" : "Applied, but not verified",
            }),
            ...lines.map((t) => el(doc, "div", { text: t })),
            el(doc, "div", {
              class: "muted",
              text: "Your real website is unchanged. Reload this page to remove the preview. To fix it for everyone, use “Fix this in KMR AI”.",
            }),
            el(doc, "div", { class: "row" }, buttons),
          ]),
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
