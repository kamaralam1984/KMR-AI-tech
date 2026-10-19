/*
 * Entry point injected into the active tab ONLY after the user turned the agent on for it.
 * Order of events: check the page -> (if sensitive: say so and stop) -> collect -> redact ->
 * show what will be sent -> user clicks Send -> background sends it. Fix requests only open
 * KMR AI in a new tab; nothing here changes the page or any repository.
 */
(function () {
  "use strict";
  const K = globalThis.KMR_EXT;
  if (window.__kmrAgentContent) return;
  Object.defineProperty(window, "__kmrAgentContent", {
    value: true,
    enumerable: false,
    configurable: true,
  });

  const runtime = { console: [], pageErrors: [], failed: [], http: [] };
  let overlay = null;
  let settings = {
    userBlocklist: [],
    confirmBeforeSend: true,
    includeScreenshot: false,
    glideMs: 700, // how long the cursor takes to arrive before a preview is applied
  };
  let lastResult = null;
  const undoAll = []; // every applied preview, so turning the agent off restores the page
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const reduceMotion = () =>
    !!(
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );

  window.addEventListener("kmr-agent-log", (event) => {
    let entry;
    try {
      entry = JSON.parse(String(event.detail));
    } catch {
      return;
    }
    const list = {
      console: runtime.console,
      page_error: runtime.pageErrors,
      failed: runtime.failed,
      http: runtime.http,
    }[entry.type];
    if (list && list.length < 40) list.push(entry);
  });

  function assess() {
    return K.sensitive.assessPage(
      document,
      location.href,
      settings.userBlocklist,
    );
  }

  function build(projectId) {
    const verdict = assess();
    if (verdict.blocked) return { blocked: true, reason: verdict.reason };
    const collected = K.collect.collectPage(document, window, runtime);
    const wantsShot = settings.includeScreenshot && collected.screenshotSafe;
    const payload = K.payload.buildPayload(collected, {
      projectId,
      pageUrl: K.redact.redactUrl(location.href, collected.counts),
      hasScreenshot: wantsShot,
    });
    return {
      blocked: false,
      payload,
      wantsShot,
      maskRects: collected.maskRects,
    };
  }

  function ensureOverlay() {
    if (!overlay) {
      // testMode is set only by the automated tests; the background never forwards it.
      overlay = K.overlay.createOverlay(
        document,
        window,
        settings.testMode ? { mode: "open", trustSynthetic: true } : {},
      );
      overlay.onOff(turnOff);
    }
    overlay.mount();
    return overlay;
  }

  /**
   * "Preview fix": one element at a time, each only after the user allows it with a real click.
   * It edits one attribute (or the title) in this tab's copy of the page; see src/fixes.js.
   */
  function startPreview(finding) {
    const ui = ensureOverlay();
    const targets = (finding.fix_targets || []).filter((t) =>
      K.fixes.supports(t.check),
    );
    let index = 0;
    const back = () => {
      if (!lastResult) return;
      showResult(ui, lastResult);
      if (undoAll.length) {
        ui.setStatus(
          `${undoAll.length} preview change(s) applied in this tab only. Your real website is unchanged.`,
        );
      }
    };
    const find = (selector) => {
      try {
        return document.querySelector(selector);
      } catch {
        return null;
      }
    };
    const step = () => {
      while (index < targets.length && !find(targets[index].selector))
        index += 1;
      if (index >= targets.length) {
        ui.setStatus("No more elements to preview.");
        return back();
      }
      const target = targets[index];
      const spec = K.fixes.FIXES[target.check];
      ui.moveCursorTo(target.selector);
      ui.askPermission({
        heading: `Allow KMR AI to change this element? (${index + 1} of ${targets.length})`,
        lines: [
          `Problem: ${finding.title}`,
          `Where: ${target.selector}`,
          `It will ${spec.what}, in this tab only. Your real website stays as it is, and you can undo it.`,
        ],
        field: { prompt: spec.prompt },
        onCancel: back,
        onAllow: async (raw, fail) => {
          const value = K.fixes.validate(target.check, raw);
          if (!value.ok) return fail(value.message);
          const verdict = assess();
          if (verdict.blocked) return ui.showBlocked(verdict.reason);
          const element = find(target.selector);
          if (!element) return fail("That element is no longer on the page.");
          ui.moveCursorTo(target.selector);
          await sleep(reduceMotion() ? 0 : settings.glideMs);
          ui.pulseCursor();
          await sleep(reduceMotion() ? 0 : Math.min(settings.glideMs, 450));
          const result = K.fixes.apply(
            document,
            element,
            target.check,
            value.value,
          );
          undoAll.push(result.undo);
          ui.markFixed(target.selector);
          ui.showApplied({
            lines: [
              K.fixes.describe(target.check, value.value, target.selector),
              result.detail,
            ],
            verified: result.verified,
            onUndo: () => {
              result.undo();
              const at = undoAll.indexOf(result.undo);
              if (at >= 0) undoAll.splice(at, 1);
              back();
              ui.setStatus("Undone. The page is back to how it was.");
            },
            onNext:
              index + 1 < targets.length
                ? () => {
                    index += 1;
                    step();
                  }
                : null,
            onDone: back,
          });
        },
      });
    };
    step();
  }

  function showResult(ui, result) {
    ui.showFindings(result, {
      onFix: (finding) =>
        chrome.runtime.sendMessage({
          type: "kmr:open",
          path: finding.web_path,
        }),
      onDismiss: (_finding, card) => card.remove(),
      onPreview: startPreview,
      canPreview: (finding) =>
        (finding.fix_targets || []).some((t) => K.fixes.supports(t.check)),
    });
  }

  function turnOff() {
    while (undoAll.length) undoAll.pop()(); // put the page back exactly as it was
    if (overlay) overlay.destroy();
    overlay = null;
    delete window.__kmrAgentContent;
    chrome.runtime.onMessage.removeListener(onMessage);
  }

  async function analyze(projectId) {
    const ui = ensureOverlay();
    const built = build(projectId);
    if (built.blocked) {
      ui.showBlocked(built.reason);
      return { blocked: true, reason: built.reason };
    }
    const send = async () => {
      ui.clearContent();
      ui.setStatus(
        "Sending the redacted page data and waiting for the analysis...",
      );
      const answer = await chrome.runtime.sendMessage({
        type: "kmr:send",
        payload: built.payload,
        wantsShot: built.wantsShot,
        maskRects: built.maskRects,
        dpr: window.devicePixelRatio || 1,
      });
      if (!answer || !answer.ok) {
        ui.clearContent();
        ui.setStatus(
          answer ? answer.message : "No answer from the extension.",
          "danger",
        );
        return;
      }
      lastResult = answer.result;
      showResult(ui, answer.result);
    };
    if (settings.confirmBeforeSend) {
      ui.showPreview(K.payload.preview(built.payload, built.wantsShot), {
        onSend: send,
        onCancel: () => {
          ui.clearContent();
          ui.setStatus("Cancelled. Nothing was sent.");
        },
      });
    } else {
      await send();
    }
    return { blocked: false };
  }

  function onMessage(message, _sender, reply) {
    if (!message || typeof message.type !== "string") return false;
    if (message.type === "kmr:configure") {
      settings = { ...settings, ...message.settings };
      const ui = ensureOverlay();
      const verdict = assess();
      if (verdict.blocked) ui.showBlocked(verdict.reason);
      else
        ui.setStatus(
          "Agent is on for this tab. It only looks; it never clicks or types.",
        );
      reply({ ok: true, blocked: verdict.blocked, reason: verdict.reason });
      return false;
    }
    if (message.type === "kmr:preview") {
      const built = build(message.projectId);
      reply(
        built.blocked
          ? { blocked: true, reason: built.reason }
          : {
              blocked: false,
              text: K.payload.preview(built.payload, built.wantsShot),
            },
      );
      return false;
    }
    if (message.type === "kmr:analyze") {
      analyze(message.projectId).then(reply);
      return true;
    }
    if (message.type === "kmr:off") {
      turnOff();
      reply({ ok: true });
      return false;
    }
    return false;
  }
  chrome.runtime.onMessage.addListener(onMessage);
})();
