"use strict";

/* The chat window. It talks only to the agent (through the main process) and never holds a token or
 * a secret. All text is inserted with textContent, never as HTML. */
(function () {
  const STR = window.KMR_STRINGS.STRINGS;
  const L = window.KMR_LOGIC;
  const $ = (id) => document.getElementById(id);
  const state = { lang: "auto", theme: "dark", status: null, working: false, selected: new Map(), windows: [], pending: null };

  const ui = () => (state.lang === "auto" ? "en" : state.lang);
  const t = (key, values) => {
    const text = (STR[ui()] && STR[ui()][key]) || STR.en[key] || key;
    return values ? L.fill(text, values) : text;
  };
  const agent = (method, path, body) => window.kmr.agent(method, path, body);

  // ----------------------------------------------------------------------------- rendering

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) node.append(c);
    return node;
  }

  function scroll() {
    const log = $("log");
    log.scrollTop = log.scrollHeight;
  }

  function say(kind, text) {
    const node = el("div", { class: `msg ${kind}`, text });
    $("log").append(node);
    scroll();
    return node;
  }

  function applyStrings() {
    $("name").textContent = t("name");
    $("text").placeholder = t("placeholder");
    $("send").setAttribute("aria-label", t("send"));
    $("look").setAttribute("aria-label", t("look"));
    $("look").title = t("look");
    $("disclaimer").textContent = L.disclaimer(state.status, STR[ui()]);
    $("status").textContent = L.statusLine(state.status, STR[ui()]);
    $("trouble-h").textContent = t("trouble");
    $("reload").textContent = t("reload");
    $("check").textContent = t("check");
    $("picker-h").textContent = t("pickTitle");
    $("picker-note").textContent = t("pickNote");
    $("picker-ok").textContent = "OK";
    $("picker-cancel").textContent = t("cancel");
    $("preview-h").textContent = t("previewTitle");
    $("preview-note").textContent = t("previewNote");
    $("preview-send").textContent = t("sendThis");
    $("preview-cancel").textContent = t("cancel");
    $("setup-h").textContent = t("setupTitle");
    $("setup-text").textContent = t("setupText");
    $("connect").textContent = t("connect");
    $("settings-h").textContent = t("settings");
    $("blocked-l").firstChild.textContent = t("blocked");
    $("builtin").textContent = t("builtIn");
    $("background-l").textContent = t("background");
    $("autostart-l").textContent = t("autostart");
    $("settings-save").textContent = t("save");
    $("disconnect").textContent = t("disconnect");
    $("quit").textContent = t("quit");
    $("settings-close").textContent = "×";
    const chips = $("chips");
    chips.replaceChildren(
      ...STR[ui()].chips.map((label, i) => {
        const b = el("button", { type: "button", class: "chip", text: label });
        b.addEventListener("click", () => ask(STR[ui()].chipMessages[i]));
        return b;
      }),
    );
    for (const b of $("lang").querySelectorAll("button")) {
      b.setAttribute("aria-checked", String(b.dataset.lang === state.lang));
      if (b.dataset.lang === "auto") b.textContent = t("auto");
    }
    const s = state.status;
    $("usage").textContent = s && s.connected && s.limit ? t("usage", { used: s.used, limit: s.limit }) : "";
    $("dot").classList.toggle("on", !!(s && s.connected));
    renderLooking();
  }

  function renderLooking() {
    const box = $("looking");
    box.replaceChildren(...[...state.selected.values()].map((w) => el("span", { text: `${w.app} — ${w.title}`.slice(0, 60) })));
    box.hidden = state.selected.size === 0;
    $("look").setAttribute("aria-pressed", String(state.selected.size > 0));
  }

  // --------------------------------------------------------------------------------- status

  async function refreshStatus() {
    const r = await agent("GET", "/status");
    state.status = r.status === 200 ? r.body : { connected: false };
    applyStrings();
    const needSetup = !state.status.has_token || !state.status.server_url;
    if (needSetup && $("setup").hidden) openSheet("setup");
    return state.status;
  }

  function openSheet(id) {
    for (const s of document.querySelectorAll(".sheet")) s.hidden = s.id !== id;
  }
  function closeSheets() {
    for (const s of document.querySelectorAll(".sheet")) s.hidden = true;
  }

  // ------------------------------------------------------------------------------- the chat

  function setWorking(on) {
    state.working = on;
    $("send").disabled = on;
    $("composer").setAttribute("aria-busy", String(on));
  }

  async function ask(message) {
    if (!L.canSend(message, state.working)) return;
    if (!state.status || !state.status.connected) {
      openSheet("setup");
      return;
    }
    setWorking(true);
    say("me", message);
    $("text").value = "";
    const prep = await agent("POST", "/prepare", { message, language: state.lang, window_ids: [...state.selected.keys()] });
    if (prep.status !== 200) {
      say("err", (prep.body && prep.body.error) || "Something went wrong.");
      return setWorking(false);
    }
    if (prep.body.windows > 0 || prep.body.blocked > 0) {
      state.pending = prep.body;
      $("preview-text").textContent = prep.body.preview;
      openSheet("preview");
      return; // the person decides: Send this / Cancel
    }
    await deliver(prep.body);
  }

  async function deliver(prepared) {
    closeSheets();
    const thinking = say("sys", t("working"));
    const r = await agent("POST", "/send", { prepared_id: prepared.prepared_id, sha256: prepared.sha256 });
    thinking.remove();
    state.selected.clear(); // each look is a fresh decision
    renderLooking();
    if (r.status === 200) {
      say("bot", r.body.reply);
      for (const step of r.body.steps || []) renderStep(step);
      refreshStatus();
    } else {
      say("err", (r.body && r.body.error) || "Something went wrong.");
    }
    setWorking(false);
  }

  // ---------------------------------------------------------------------------------- steps

  function renderStep(step) {
    const expiresAt = Date.now() + 120000;
    const result = el("div", { class: "result" });
    const clock = el("small", { class: "hint" });
    const buttons = el("div", { class: "row" });
    let input = null;
    const card = el("div", { class: "step", "data-kind": step.kind, "data-step": step.id }, [
      el("h3", { text: L.stepTitle(step, STR[ui()]) }),
      el("div", { class: "why", text: step.why || "" }),
    ]);

    const finish = (text, ok) => {
      result.textContent = text;
      result.className = `result ${ok ? "ok" : "bad"}`;
      buttons.remove();
      if (input) input.disabled = true;
      clearInterval(timer);
      clock.remove();
    };
    const timer = setInterval(() => {
      const left = L.secondsLeft(expiresAt, Date.now());
      clock.textContent = t("expires", { t: L.clock(left) });
      if (left === 0 && step.kind !== "highlight") finish(t("expired"), false);
    }, 1000);

    const post = (action, body) => agent("POST", `/steps/${step.id}/${action}`, body || {});

    if (step.kind === "highlight") {
      const where = el("div", { class: "hint" });
      const show = el("button", { type: "button", class: "ghost", text: t("showWhere") });
      show.addEventListener("click", async () => {
        const r = await post("highlight");
        where.textContent = r.status === 200 ? L.describeWhere(r.body, "") : (r.body && r.body.error) || "";
      });
      buttons.append(show);
      card.append(buttons, where);
    } else {
      if (step.kind === "type") {
        input = el("input", { type: "text", maxlength: "120", "aria-label": t("stepType") });
        input.value = step.suggested_text || "";
        card.append(input, el("div", { class: "hint", text: t("typeHint") }));
      }
      const allow = el("button", { type: "button", class: "gold", text: t("allow") });
      const decline = el("button", { type: "button", class: "ghost", text: t("decline") });
      allow.addEventListener("click", async (event) => {
        if (!event.isTrusted) return; // only a real click on this button counts as permission
        allow.disabled = true;
        const r = await post("approve", step.kind === "type" ? { text: input.value } : {});
        if (r.status === 200) finish(`${t("done")} — ${r.body.detail || ""}`.trim(), true);
        else if (r.status === 422) {
          allow.disabled = false; // the text was refused; the step stays open so it can be fixed
          result.textContent = r.body.error;
          result.className = "result bad";
        } else finish(`${t("notDone")}: ${(r.body && r.body.error) || ""}`, false);
      });
      decline.addEventListener("click", async (event) => {
        if (!event.isTrusted) return;
        await post("reject");
        finish(t("declined"), false);
      });
      buttons.append(allow, decline);
      card.append(buttons);
    }
    card.append(result, clock);
    $("log").append(card);
    scroll();
  }

  // ------------------------------------------------------------------------------ the picker

  async function openPicker() {
    const r = await agent("GET", "/windows");
    if (r.status !== 200) return say("err", (r.body && r.body.error) || "Could not list windows.");
    const list = $("windows");
    list.replaceChildren();
    if (!r.body.windows.length) list.append(el("li", { class: "note", text: t("noWindows") }));
    for (const w of r.body.windows) {
      const box = el("input", { type: "checkbox", "data-id": w.window_id });
      box.checked = state.selected.has(w.window_id);
      box.addEventListener("change", () => {
        if (box.checked && [...list.querySelectorAll("input:checked")].length > 5) box.checked = false;
      });
      list.append(el("li", {}, [el("label", {}, [box, el("span", {}, [document.createTextNode(w.app), el("small", { text: w.title })])])]));
    }
    state.windows = r.body.windows;
    $("hidden").textContent = r.body.hidden ? t("hiddenWindows", { n: r.body.hidden }) : "";
    openSheet("picker");
  }

  // -------------------------------------------------------------------------------- settings

  async function openSettings() {
    const status = state.status || {};
    $("blocked").value = (status.extra_apps || []).join("\n");
    const prefs = await window.kmr.prefs();
    $("background").checked = prefs.background !== false;
    $("autostart").checked = !!prefs.autostart;
    openSheet("settings");
  }

  // ------------------------------------------------------------------------------ wire it up

  function wire() {
    $("composer").addEventListener("submit", (event) => {
      event.preventDefault();
      ask($("text").value);
    });
    $("look").addEventListener("click", openPicker);
    $("picker-cancel").addEventListener("click", closeSheets);
    $("picker-ok").addEventListener("click", () => {
      state.selected.clear();
      for (const box of $("windows").querySelectorAll("input:checked")) {
        const w = state.windows.find((x) => x.window_id === box.dataset.id);
        if (w) state.selected.set(w.window_id, w);
      }
      renderLooking();
      closeSheets();
      $("text").focus();
    });
    $("preview-send").addEventListener("click", async (event) => {
      if (!event.isTrusted || !state.pending) return;
      const p = state.pending;
      state.pending = null;
      await deliver(p);
    });
    $("preview-cancel").addEventListener("click", () => {
      state.pending = null;
      closeSheets();
      say("sys", "Cancelled. Nothing was sent.");
      setWorking(false);
    });
    $("reload").addEventListener("click", () => ask(t("reloadMessage")));
    $("check").addEventListener("click", () => ask(t("checkMessage")));
    $("connect").addEventListener("click", async () => {
      $("setup-error").textContent = "";
      const r = await agent("POST", "/config", { server_url: $("server").value, token: $("token").value });
      if (r.status !== 200) return ($("setup-error").textContent = (r.body && r.body.error) || "Could not save.");
      $("token").value = "";
      const status = await refreshStatus();
      if (status.connected) closeSheets();
      else $("setup-error").textContent = status.error || "Could not connect. Check the address and token.";
    });
    $("lang").addEventListener("click", async (event) => {
      const b = event.target.closest("button[data-lang]");
      if (!b) return;
      state.lang = b.dataset.lang;
      await window.kmr.setPref("lang", state.lang);
      applyStrings();
    });
    $("theme").addEventListener("click", async () => {
      state.theme = state.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = state.theme;
      await window.kmr.setPref("theme", state.theme);
    });
    $("gear").addEventListener("click", openSettings);
    $("settings-close").addEventListener("click", closeSheets);
    $("settings-save").addEventListener("click", async () => {
      const apps = $("blocked").value.split("\n").map((x) => x.trim()).filter(Boolean);
      await agent("POST", "/config", { extra_apps: apps });
      await window.kmr.setPref("background", $("background").checked);
      await window.kmr.setAutostart($("autostart").checked);
      await refreshStatus();
      closeSheets();
    });
    $("disconnect").addEventListener("click", async () => {
      await agent("POST", "/config", { disconnect: true });
      await refreshStatus();
    });
    $("quit").addEventListener("click", () => window.kmr.quit());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeSheets();
    });
  }

  async function init() {
    const prefs = await window.kmr.prefs();
    state.theme = prefs.theme === "light" ? "light" : "dark";
    state.lang = ["auto", "en", "hinglish", "hi"].includes(prefs.lang) ? prefs.lang : "auto";
    document.documentElement.dataset.theme = state.theme;
    wire();
    applyStrings();
    say("bot", t("hello"));
    await refreshStatus();
    setInterval(refreshStatus, 20000);
  }
  init();
})();
