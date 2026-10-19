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
  let settings = { userBlocklist: [], confirmBeforeSend: true, includeScreenshot: false };

  window.addEventListener("kmr-agent-log", (event) => {
    let entry;
    try {
      entry = JSON.parse(String(event.detail));
    } catch {
      return;
    }
    const list = { console: runtime.console, page_error: runtime.pageErrors, failed: runtime.failed, http: runtime.http }[entry.type];
    if (list && list.length < 40) list.push(entry);
  });

  function assess() {
    return K.sensitive.assessPage(document, location.href, settings.userBlocklist);
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
    return { blocked: false, payload, wantsShot, maskRects: collected.maskRects };
  }

  function ensureOverlay() {
    if (!overlay) {
      overlay = K.overlay.createOverlay(document, window);
      overlay.onOff(turnOff);
    }
    overlay.mount();
    return overlay;
  }

  function turnOff() {
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
      ui.setStatus("Sending the redacted page data and waiting for the analysis...");
      const answer = await chrome.runtime.sendMessage({
        type: "kmr:send",
        payload: built.payload,
        wantsShot: built.wantsShot,
        maskRects: built.maskRects,
        dpr: window.devicePixelRatio || 1,
      });
      if (!answer || !answer.ok) {
        ui.clearContent();
        ui.setStatus(answer ? answer.message : "No answer from the extension.", "danger");
        return;
      }
      ui.showFindings(answer.result, {
        onFix: (finding) => chrome.runtime.sendMessage({ type: "kmr:open", path: finding.web_path }),
        onDismiss: (_finding, card) => card.remove(),
      });
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
      else ui.setStatus("Agent is on for this tab. It only looks; it never clicks or types.");
      reply({ ok: true, blocked: verdict.blocked, reason: verdict.reason });
      return false;
    }
    if (message.type === "kmr:preview") {
      const built = build(message.projectId);
      reply(
        built.blocked
          ? { blocked: true, reason: built.reason }
          : { blocked: false, text: K.payload.preview(built.payload, built.wantsShot) },
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
