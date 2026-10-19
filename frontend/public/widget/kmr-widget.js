/*
 * KMR AI visitor widget. One file, no dependencies, no build step.
 *
 *   <script src=".../widget/kmr-widget.js" data-api=".../widget-api"
 *           data-install-key="kmr_ik_..." defer></script>
 *
 * Rules this file follows:
 *  - Everything from the server is shown with textContent, never as HTML.
 *  - Files chosen for a form are inspected in this browser (type, size, pixels). They are never
 *    uploaded, read into the page, or sent to any server.
 *  - Nothing is stored in the browser (no cookies and no web storage of any kind), and requests
 *    are sent without credentials.
 *  - The widget never submits a host form, pays, or ticks a declaration. A confirmation is a
 *    token the visitor requested by typing a phrase; the host page receives it as a DOM event
 *    ("kmr:confirmation") and decides what to do.
 */
(function (root) {
  "use strict";

  // ----------------------------------------------------------------------- pure logic (tested)

  var MIME_NAMES = { "image/jpeg": "JPEG", "image/png": "PNG", "application/pdf": "PDF" };

  /** Mirrors backend/app/assistant/forms.py:check_file. Both run the same shared fixture. */
  function checkFile(spec, meta) {
    if (!meta || typeof meta !== "object") return { status: "missing", message: "Choose a file." };
    var size = meta.size_bytes;
    if (typeof size !== "number" || !isFinite(size) || size < 0 || Math.floor(size) !== size) {
      return { status: "invalid", message: "The file size could not be read. Choose it again." };
    }
    if ((spec.accept || []).indexOf(meta.mime) === -1) {
      var allowed = (spec.accept || [])
        .map(function (m) {
          return MIME_NAMES[m] || m;
        })
        .join(", ");
      return {
        status: "invalid",
        message: "This file type is not accepted. Use: " + allowed + ".",
      };
    }
    var kb = size / 1024;
    if (spec.max_kb != null && kb > spec.max_kb) {
      return {
        status: "invalid",
        message: "The file is " + Math.round(kb) + " KB; the maximum is " + spec.max_kb + " KB.",
      };
    }
    if (spec.min_kb != null && kb < spec.min_kb) {
      return {
        status: "invalid",
        message: "The file is " + Math.round(kb) + " KB; the minimum is " + spec.min_kb + " KB.",
      };
    }
    if (String(meta.mime).indexOf("image/") === 0) {
      var bounded = [spec.min_width, spec.max_width, spec.min_height, spec.max_height];
      if (
        bounded.some(function (v) {
          return v != null;
        })
      ) {
        var w = meta.width,
          h = meta.height;
        if (!Number.isInteger(w) || !Number.isInteger(h)) {
          return { status: "invalid", message: "The image size in pixels could not be read." };
        }
        var dims = [
          [w, spec.min_width, spec.max_width, "width"],
          [h, spec.min_height, spec.max_height, "height"],
        ];
        for (var i = 0; i < dims.length; i++) {
          var d = dims[i];
          if (d[1] != null && d[0] < d[1]) {
            return {
              status: "invalid",
              message:
                "The image " + d[3] + " is " + d[0] + "px; it must be at least " + d[1] + "px.",
            };
          }
          if (d[2] != null && d[0] > d[2]) {
            return {
              status: "invalid",
              message:
                "The image " + d[3] + " is " + d[0] + "px; it must be at most " + d[2] + "px.",
            };
          }
        }
      }
    }
    return { status: "ok", message: "The file meets the stated requirements." };
  }

  /**
   * Works out how to shrink an image to fit a spec, without touching any pixels: a scale factor
   * for the dimensions and a list of JPEG qualities to try in order. Returns null when no resize
   * can help (wrong type, or the image is too small to enlarge honestly).
   */
  function planResize(meta, spec) {
    if (!meta || String(meta.mime).indexOf("image/") !== 0) return null;
    if ((spec.accept || []).indexOf("image/jpeg") === -1) return null;
    var w = meta.width,
      h = meta.height;
    if (!Number.isInteger(w) || !Number.isInteger(h)) return null;
    if (
      (spec.min_width != null && w < spec.min_width) ||
      (spec.min_height != null && h < spec.min_height)
    ) {
      return null; // would need enlarging, which invents detail
    }
    var scale = 1;
    if (spec.max_width != null && w > spec.max_width) scale = Math.min(scale, spec.max_width / w);
    if (spec.max_height != null && h > spec.max_height)
      scale = Math.min(scale, spec.max_height / h);
    var tw = Math.floor(w * scale),
      th = Math.floor(h * scale);
    if (
      (spec.min_width != null && tw < spec.min_width) ||
      (spec.min_height != null && th < spec.min_height)
    ) {
      return null;
    }
    return { width: tw, height: th, qualities: [0.92, 0.85, 0.75, 0.65, 0.55, 0.45] };
  }

  var core = { checkFile: checkFile, planResize: planResize };
  root.KMRWidgetCore = core;

  // ----------------------------------------------------------------------- browser UI

  var script = root.document && root.document.currentScript;
  if (!script || !script.getAttribute || !script.getAttribute("data-install-key")) return;
  var API = (script.getAttribute("data-api") || "").replace(/\/$/, "");
  var KEY = script.getAttribute("data-install-key");
  // A guardian-consent token the installer's own page hands to the learner (see
  // docs/CHILD-LEARNING-SAFEGUARDS.md). Kept in memory only; sent as a header, never stored.
  var CONSENT = script.getAttribute("data-consent-token") || "";
  root.KMRWidget = {
    setConsent: function (token) {
      CONSENT = typeof token === "string" ? token.slice(0, 120) : "";
    },
  };
  var doc = root.document;

  function el(tag, attrs, children) {
    var node = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else if (k === "class") node.className = attrs[k];
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }
  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  async function api(path, body) {
    var res = await root.fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "omit",
      headers: Object.assign(
        { "X-KMR-Install-Key": KEY, "Content-Type": "application/json" },
        CONSENT ? { "X-KMR-Consent": CONSENT } : {},
      ),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    var data = null;
    try {
      data = await res.json();
    } catch (e) {
      /* not JSON */
    }
    if (!res.ok) {
      var msg =
        (data && data.error && data.error.message) || "Something went wrong (" + res.status + ").";
      var err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  var STYLE =
    ":host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
    ".fab{position:fixed;right:16px;bottom:16px;z-index:2147483000;background:#1d4ed8;color:#fff;border:0;border-radius:999px;padding:12px 18px;font-size:15px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25)}" +
    ".fab:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}" +
    ".panel{position:fixed;right:16px;bottom:72px;z-index:2147483000;width:min(380px,calc(100vw - 32px));max-height:min(620px,calc(100vh - 100px));display:flex;flex-direction:column;background:#fff;color:#111827;border:1px solid #d1d5db;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.25);overflow:hidden}" +
    ".panel[hidden]{display:none}.head{padding:10px 14px;background:#111827;color:#fff;font-weight:600;display:flex;justify-content:space-between;align-items:center;gap:8px}" +
    ".head button{background:none;border:0;color:#fff;font-size:20px;cursor:pointer}.tabs{display:flex;gap:4px;padding:8px 8px 0;flex-wrap:wrap}" +
    ".tab{border:1px solid #d1d5db;background:#f9fafb;border-radius:8px 8px 0 0;padding:6px 10px;font-size:13px;cursor:pointer}.tab[aria-selected=true]{background:#fff;border-bottom-color:#fff;font-weight:600}" +
    ".body{padding:12px 14px;overflow:auto;font-size:14px;line-height:1.45}.note{font-size:12px;color:#4b5563;margin:6px 0}" +
    "label{display:block;font-weight:600;margin:10px 0 3px}input,select,textarea{width:100%;font-size:16px;padding:7px 9px;border:1px solid #9ca3af;border-radius:6px}" +
    "button.act{margin-top:10px;background:#1d4ed8;color:#fff;border:0;border-radius:8px;padding:9px 14px;font-size:14px;cursor:pointer}button.act[disabled]{opacity:.5;cursor:not-allowed}" +
    ".quote{border-left:3px solid #1d4ed8;padding:4px 10px;margin:8px 0;background:#f3f4f6}.src{font-size:12px}.src a{color:#1d4ed8}" +
    ".ok{color:#166534}.bad{color:#b91c1c}.warn{color:#92400e}.status{font-weight:600}.row{border-bottom:1px solid #e5e7eb;padding:6px 0}" +
    ".err{color:#b91c1c;margin-top:8px}@media (prefers-reduced-motion:no-preference){.panel{transition:opacity .15s}}";

  var host = el("div", { id: "kmr-ai-widget" });
  var shadow = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  shadow.appendChild(el("style", { text: STYLE }));
  var panel = el("section", {
    class: "panel",
    role: "dialog",
    "aria-label": "Help assistant",
    hidden: "",
  });
  var fab = el("button", { class: "fab", type: "button", "aria-expanded": "false", text: "Help" });
  var live = el("div", { "aria-live": "polite", class: "body" });
  var tabsBar = el("div", { class: "tabs", role: "tablist" });
  var head = el("div", { class: "head" }, [
    el("span", { id: "kmr-title", text: "Help" }),
    el("button", { type: "button", "aria-label": "Close help", text: "×", onclick: close }),
  ]);
  panel.appendChild(head);
  panel.appendChild(tabsBar);
  panel.appendChild(live);
  shadow.appendChild(panel);
  shadow.appendChild(fab);

  var config = null;
  var current = null;

  function open() {
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    if (!config) load();
  }
  function close() {
    panel.hidden = true;
    fab.setAttribute("aria-expanded", "false");
    fab.focus();
  }
  fab.addEventListener("click", function () {
    panel.hidden ? open() : close();
  });
  panel.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });

  function message(text, cls) {
    return el("p", { class: cls || "note", text: text });
  }

  async function load() {
    clear(live);
    live.appendChild(message("Loading…"));
    try {
      config = await api("/config");
    } catch (e) {
      clear(live);
      live.appendChild(message(e.message, "err"));
      return;
    }
    head.firstChild.textContent = config.name || "Help";
    var tabs = [];
    if (config.modules.indexOf("guide") > -1)
      tabs.push(["guide", "Ask about this site", renderGuide]);
    if (config.modules.indexOf("forms") > -1 && config.forms.length)
      tabs.push(["forms", "Form help", renderForms]);
    if (config.modules.indexOf("learning") > -1) tabs.push(["learning", "Learn", renderLearning]);
    tabs.push(["handoff", "Talk to support", renderHandoff]);
    clear(tabsBar);
    tabs.forEach(function (t) {
      tabsBar.appendChild(
        el("button", {
          class: "tab",
          type: "button",
          role: "tab",
          "aria-selected": "false",
          "data-tab": t[0],
          text: t[1],
          onclick: function () {
            select(t, tabs);
          },
        }),
      );
    });
    select(tabs[0], tabs);
  }

  function select(tab, tabs) {
    Array.prototype.forEach.call(tabsBar.children, function (b) {
      b.setAttribute("aria-selected", String(b.getAttribute("data-tab") === tab[0]));
    });
    current = tab[0];
    clear(live);
    live.appendChild(message(config.notice, "note"));
    tab[2](live);
  }

  function field(label, input) {
    var id = "f" + Math.random().toString(36).slice(2, 9);
    input.setAttribute("id", id);
    return el("div", {}, [el("label", { for: id, text: label }), input]);
  }
  function busy(button, on) {
    button.disabled = on;
  }
  function show(container, node) {
    var old = container.querySelector("[data-result]");
    if (old) old.remove();
    node.setAttribute("data-result", "1");
    container.appendChild(node);
  }

  // ---- 11A guide
  function renderGuide(box) {
    var q = el("input", { type: "text", maxlength: "300", autocomplete: "off" });
    var go = el("button", { class: "act", type: "button", text: "Ask" });
    box.appendChild(field("Your question about this website", q));
    box.appendChild(go);
    async function ask() {
      if (!q.value.trim()) return;
      busy(go, true);
      var out = el("div");
      try {
        var r = await api("/guide/ask", { question: q.value });
        out.appendChild(message(r.message, r.found ? "note" : "warn"));
        r.passages.forEach(function (p) {
          var src = el("div", { class: "src" });
          if (p.source_url && /^https?:\/\//.test(p.source_url)) {
            src.appendChild(
              el("a", {
                href: p.source_url,
                target: "_blank",
                rel: "noopener noreferrer",
                text: p.title || p.source_url,
              }),
            );
          } else src.textContent = p.title;
          out.appendChild(el("div", { class: "quote" }, [el("div", { text: p.text }), src]));
        });
      } catch (e) {
        out.appendChild(message(e.message, "err"));
      }
      busy(go, false);
      show(box, out);
    }
    go.addEventListener("click", ask);
    q.addEventListener("keydown", function (e) {
      if (e.key === "Enter") ask();
    });
  }

  // ---- 11B forms
  function renderForms(box) {
    var pick = el("select");
    config.forms.forEach(function (f) {
      pick.appendChild(el("option", { value: f.id, text: f.name }));
    });
    var area = el("div");
    box.appendChild(field("Which form?", pick));
    box.appendChild(area);
    pick.addEventListener("change", function () {
      loadForm(pick.value, area);
    });
    loadForm(pick.value, area);
  }

  async function loadForm(id, area) {
    clear(area);
    area.appendChild(message("Loading form…"));
    var defn;
    try {
      defn = await api("/forms/" + encodeURIComponent(id));
    } catch (e) {
      clear(area);
      area.appendChild(message(e.message, "err"));
      return;
    }
    clear(area);
    area.appendChild(message(defn.notice));
    var values = {};
    var inputs = {};
    defn.fields.forEach(function (f) {
      var input;
      if (f.type === "select") {
        input = el("select");
        input.appendChild(el("option", { value: "", text: "Choose…" }));
        f.options.forEach(function (o) {
          input.appendChild(el("option", { value: o, text: o }));
        });
      } else if (f.type === "declaration") {
        area.appendChild(
          el("div", { class: "row" }, [
            el("strong", { text: f.label }),
            el("p", { text: f.declaration_text }),
            message("You confirm this yourself in the last step. I cannot tick it for you."),
          ]),
        );
        return;
      } else if (f.type === "file") {
        input = el("input", { type: "file", accept: (f.accept || []).join(",") });
      } else {
        input = el("input", {
          type: f.type === "date" ? "text" : f.type === "number" ? "text" : "text",
          inputmode: f.type === "phone" || f.type === "number" ? "numeric" : "text",
          placeholder: f.type === "date" ? "DD/MM/YYYY" : "",
          autocomplete: "off",
        });
      }
      inputs[f.key] = { input: input, spec: f };
      var wrap = field(f.label + (f.required ? " *" : ""), input);
      if (f.help) wrap.appendChild(message(f.help));
      var fileNote = el("div", { class: "note", "aria-live": "polite" });
      wrap.appendChild(fileNote);
      if (f.type === "file")
        input.addEventListener("change", function () {
          inspectFile(input, f, fileNote, values);
        });
      area.appendChild(wrap);
    });
    var check = el("button", { class: "act", type: "button", text: "Check my answers" });
    var result = el("div");
    area.appendChild(check);
    area.appendChild(result);
    check.addEventListener("click", async function () {
      Object.keys(inputs).forEach(function (k) {
        if (inputs[k].spec.type !== "file") values[k] = inputs[k].input.value;
      });
      busy(check, true);
      clear(result);
      try {
        var r = await api("/forms/" + encodeURIComponent(id) + "/check", { values: values });
        renderReview(result, r, id, defn, values);
      } catch (e) {
        result.appendChild(message(e.message, "err"));
      }
      busy(check, false);
    });
  }

  async function inspectFile(input, spec, note, values) {
    var file = input.files && input.files[0];
    if (!file) {
      delete values[spec.key];
      note.textContent = "";
      return;
    }
    var meta = { name: file.name, mime: file.type, size_bytes: file.size };
    if (file.type.indexOf("image/") === 0 && root.createImageBitmap) {
      try {
        var bmp = await root.createImageBitmap(file); // decoded locally, never uploaded
        meta.width = bmp.width;
        meta.height = bmp.height;
        if (bmp.close) bmp.close();
      } catch (e) {
        /* leave pixels unknown: the check then says so */
      }
    }
    values[spec.key] = meta;
    var r = checkFile(spec, meta);
    note.textContent = r.message + " (checked in your browser; the file was not uploaded)";
    note.className = "note " + (r.status === "ok" ? "ok" : "bad");
    var plan = r.status === "invalid" ? planResize(meta, spec) : null;
    if (plan && root.document.createElement("canvas").getContext) {
      note.appendChild(
        el("button", {
          class: "act",
          type: "button",
          text: "Make a smaller copy for me",
          onclick: function () {
            resizeLocally(file, spec, plan, note);
          },
        }),
      );
    }
  }

  function resizeLocally(file, spec, plan, note) {
    root.createImageBitmap(file).then(function (bmp) {
      var canvas = doc.createElement("canvas");
      canvas.width = plan.width;
      canvas.height = plan.height;
      canvas.getContext("2d").drawImage(bmp, 0, 0, plan.width, plan.height);
      var tries = plan.qualities.slice();
      (function attempt() {
        var q = tries.shift();
        if (q === undefined) {
          note.textContent = "Could not make the file small enough. Try a different photo.";
          return;
        }
        canvas.toBlob(
          function (blob) {
            if (blob && (spec.max_kb == null || blob.size / 1024 <= spec.max_kb)) {
              var url = root.URL.createObjectURL(blob);
              var link = el("a", {
                href: url,
                download: "resized.jpg",
                text:
                  "Download the smaller copy (" +
                  Math.round(blob.size / 1024) +
                  " KB), then choose it above.",
              });
              note.textContent = "";
              note.appendChild(link);
            } else attempt();
          },
          "image/jpeg",
          q,
        );
      })();
    });
  }

  function renderReview(box, r, formId, defn, values) {
    r.fields.forEach(function (f) {
      var cls = f.status === "ok" ? "ok" : f.status === "needs_confirmation" ? "warn" : "bad";
      var icon = f.status === "ok" ? "✔ " : f.status === "needs_confirmation" ? "• " : "✘ ";
      var row = el("div", { class: "row" }, [
        el("div", {
          class: "status " + cls,
          text: icon + f.label + ": " + f.status.replace("_", " "),
        }),
        el("div", { text: f.message }),
      ]);
      if (f.value != null && typeof f.value === "string")
        row.appendChild(el("div", { class: "note", text: "Read as: " + f.value }));
      if (f.ask) row.appendChild(el("div", { class: "warn", text: f.ask }));
      box.appendChild(row);
    });
    var list = el("ul");
    r.checklist.forEach(function (c) {
      list.appendChild(el("li", { text: c }));
    });
    box.appendChild(list);
    box.appendChild(
      message(
        r.ready_for_review
          ? "Nothing is missing or invalid. Nothing has been submitted."
          : r.blocking + " field(s) need attention. Nothing has been submitted.",
        r.ready_for_review ? "ok" : "bad",
      ),
    );
    if (!r.ready_for_review) return;

    var actions = [];
    defn.fields
      .filter(function (f) {
        return f.type === "declaration";
      })
      .forEach(function (f) {
        actions.push({
          label: "Confirm declaration: " + f.label,
          action: "declaration",
          field_key: f.key,
          phrase: "I CONFIRM THIS DECLARATION",
        });
      });
    actions.push({
      label: "Confirm that I want to submit",
      action: "submission",
      phrase: "I CONFIRM TO SUBMIT",
    });
    if (defn.payment_note)
      actions.push({
        label: "Confirm the payment step",
        action: "payment",
        phrase: "I CONFIRM THE PAYMENT",
      });
    box.appendChild(
      message(
        "Each confirmation is separate. To confirm one, type the phrase shown. I never submit, pay, or declare anything myself.",
      ),
    );
    actions.forEach(function (a) {
      var typed = el("input", { type: "text", autocomplete: "off", placeholder: a.phrase });
      var btn = el("button", { class: "act", type: "button", text: a.label });
      var out = el("div", { "aria-live": "polite" });
      btn.addEventListener("click", async function () {
        busy(btn, true);
        clear(out);
        try {
          var c = await api("/forms/" + encodeURIComponent(formId) + "/confirm", {
            action: a.action,
            field_key: a.field_key,
            values: values,
            phrase: typed.value,
          });
          out.appendChild(message(c.message, "ok"));
          root.dispatchEvent(
            new root.CustomEvent("kmr:confirmation", {
              detail: {
                formId: formId,
                action: c.action,
                token: c.token,
                valuesHash: c.values_hash,
                fieldKey: a.field_key || null,
              },
            }),
          );
        } catch (e) {
          out.appendChild(message(e.message, "err"));
        }
        busy(btn, false);
      });
      box.appendChild(el("div", { class: "row" }, [field("Type: " + a.phrase, typed), btn, out]));
    });
  }

  // ---- 11C learning
  function renderLearning(box) {
    var L = config.learning || {};
    box.appendChild(message(L.status || "Beta."));
    box.appendChild(message("Typed questions only. Pictures are not supported."));
    var band = el("select");
    (L.age_bands || []).forEach(function (b) {
      band.appendChild(el("option", { value: b, text: b === "18+" ? "18 or older" : "Age " + b }));
    });
    var guardian = el("input", { type: "checkbox" });
    var gl = el("label", {}, [
      guardian,
      el("span", { text: " A parent or guardian has agreed that I can use this." }),
    ]);
    if (L.consent_required) {
      // A checkbox is only a claim. Under-18 learners need the consent link from their school.
      gl = message(
        CONSENT
          ? "Your school or parent has given consent for you to use this."
          : "Under 18? Ask your school or parent for the consent link to use this helper.",
      );
    }
    var lang = el("select");
    [
      ["auto", "Same as my question"],
      ["en", "English"],
      ["hi", "हिन्दी"],
      ["hinglish", "Hinglish"],
    ].forEach(function (o) {
      lang.appendChild(el("option", { value: o[0], text: o[1] }));
    });
    var q = el("textarea", { rows: "3", maxlength: "300" });
    var go = el("button", { class: "act", type: "button", text: "Help me with this" });
    [
      field("Age group", band),
      gl,
      field("Language", lang),
      field("Your question (no names or phone numbers please)", q),
      go,
    ].forEach(function (n) {
      box.appendChild(n);
    });
    go.addEventListener("click", async function () {
      if (!q.value.trim()) return;
      busy(go, true);
      var out = el("div");
      try {
        var r = await api("/learning/ask", {
          question: q.value,
          age_band: band.value,
          guardian_confirmed: guardian.checked,
          language: lang.value,
        });
        out.appendChild(el("p", { class: "status", text: r.message }));
        (r.steps || []).forEach(function (s) {
          out.appendChild(el("div", { class: "row", text: s }));
        });
        (r.options || []).forEach(function (o) {
          out.appendChild(el("div", { class: "quote", text: o }));
        });
        (r.passages || []).forEach(function (p) {
          out.appendChild(
            el("div", { class: "quote" }, [
              el("div", { text: p.text }),
              el("div", { class: "src", text: p.title }),
            ]),
          );
        });
        (r.notes || []).forEach(function (n) {
          out.appendChild(message(n));
        });
      } catch (e) {
        out.appendChild(message(e.message, "err"));
      }
      busy(go, false);
      show(box, out);
    });
  }

  // ---- hand-off to the site's support team
  function renderHandoff(box) {
    box.appendChild(
      message(
        "If I could not help, you can send a short note to this website's support team. You will see exactly what is sent before it goes.",
      ),
    );
    var summary = el("textarea", { rows: "4", maxlength: "2000" });
    var contact = el("input", {
      type: "text",
      maxlength: "200",
      placeholder: "Email or mobile (optional)",
      autocomplete: "off",
    });
    var prev = el("button", { class: "act", type: "button", text: "Preview what will be sent" });
    var area = el("div");
    [
      field("What do you need help with?", summary),
      field("How can they reach you? (optional)", contact),
      prev,
      area,
    ].forEach(function (n) {
      box.appendChild(n);
    });
    prev.addEventListener("click", async function () {
      if (!summary.value.trim()) return;
      busy(prev, true);
      clear(area);
      try {
        var p = await api("/handoff/preview", {
          summary: summary.value,
          contact: contact.value.trim() || null,
        });
        area.appendChild(message(p.notice));
        area.appendChild(el("div", { class: "quote", text: p.text }));
        if (p.contact) area.appendChild(message("Contact: " + p.contact));
        var agree = el("input", { type: "checkbox" });
        var send = el("button", { class: "act", type: "button", text: "Send to support" });
        var out = el("div", { "aria-live": "polite" });
        area.appendChild(
          el("label", {}, [
            agree,
            el("span", { text: " " + p.consent_text + " (kept " + p.retention_days + " days)" }),
          ]),
        );
        area.appendChild(send);
        area.appendChild(out);
        send.addEventListener("click", async function () {
          clear(out);
          busy(send, true);
          try {
            await api("/handoff", {
              text: p.text,
              contact: p.contact,
              token: p.token,
              consent: agree.checked,
            });
            out.appendChild(message("Sent. The support team can now see the text above.", "ok"));
          } catch (e) {
            out.appendChild(message(e.message, "err"));
            busy(send, false);
          }
        });
      } catch (e) {
        area.appendChild(message(e.message, "err"));
      }
      busy(prev, false);
    });
  }

  doc.body.appendChild(host);
})(typeof globalThis !== "undefined" ? globalThis : window);
