/*
 * Service worker. The only place that holds the API token and talks to the KMR AI server.
 * Page scripts never see the token. Every send re-checks the page address on its own.
 */
"use strict";
importScripts("blocklist.js", "redact.js", "sensitive.js");

const K = globalThis.KMR_EXT;
const SCRIPTS = [
  "src/blocklist.js",
  "src/redact.js",
  "src/sensitive.js",
  "src/collect.js",
  "src/payload.js",
  "src/overlay.js",
  "src/content.js",
];
const DEFAULTS = {
  apiBase: "",
  webBase: "",
  token: "",
  projectId: "",
  userBlocklist: [],
  confirmBeforeSend: true,
  includeScreenshot: false,
};

async function getSettings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

function publicSettings(s) {
  // What page-facing code may know. No token, no server address.
  return {
    userBlocklist: s.userBlocklist,
    confirmBeforeSend: s.confirmBeforeSend,
    includeScreenshot: s.includeScreenshot,
  };
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

async function activate() {
  const tab = await activeTab();
  if (!tab || !tab.id || !tab.url) return { ok: false, message: "No page to look at." };
  const settings = await getSettings();
  const reason = K.sensitive.addressReason(tab.url, settings.userBlocklist);
  if (reason) {
    // Inject only enough to say so; no collection code or hook runs on this page.
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: SCRIPTS });
    await chrome.tabs.sendMessage(tab.id, { type: "kmr:configure", settings: publicSettings(settings) });
    return { ok: true, blocked: true, reason };
  }
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: SCRIPTS });
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["src/hook.js"],
    world: "MAIN",
  });
  const answer = await chrome.tabs.sendMessage(tab.id, {
    type: "kmr:configure",
    settings: publicSettings(settings),
  });
  return { ok: true, blocked: !!(answer && answer.blocked), reason: answer && answer.reason };
}

async function maskScreenshot(dataUrl, rects, dpr) {
  const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());
  const scale = Math.min(1, 1280 / bitmap.width);
  const canvas = new OffscreenCanvas(Math.round(bitmap.width * scale), Math.round(bitmap.height * scale));
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#000";
  for (const r of rects) {
    ctx.fillRect(
      Math.floor(r.x * dpr * scale) - 2,
      Math.floor(r.y * dpr * scale) - 2,
      Math.ceil(r.w * dpr * scale) + 4,
      Math.ceil(r.h * dpr * scale) + 4,
    );
  }
  const blob = await canvas.convertToBlob({ type: "image/png" });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  if (bytes.length > 1_000_000) return null;
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function api(settings, path, body) {
  const response = await fetch(`${settings.apiBase.replace(/\/$/, "")}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${settings.token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit",
  });
  let json = null;
  try {
    json = await response.json();
  } catch {
    /* not JSON */
  }
  if (!response.ok) {
    const message = (json && json.error && json.error.message) || `The server answered ${response.status}.`;
    throw new Error(message);
  }
  return json;
}

async function send(message, sender) {
  const settings = await getSettings();
  if (!settings.apiBase || !settings.token || !settings.projectId) {
    return { ok: false, message: "Set the server address, token and project in the extension options first." };
  }
  const tab = sender.tab;
  if (!tab || !tab.url) return { ok: false, message: "No page to send." };
  // Second lock, independent of the page script: the tab's real address must be allowed.
  const reason = K.sensitive.addressReason(tab.url, settings.userBlocklist);
  if (reason) return { ok: false, message: `Not sent: ${reason}.` };
  const payload = message.payload;
  if (!payload || payload.client.sensitive_page) return { ok: false, message: "Not sent: the page is sensitive." };
  payload.project_id = settings.projectId;
  if (new URL(tab.url).host !== new URL(payload.page_url).host) {
    return { ok: false, message: "Not sent: the page address changed." };
  }
  try {
    if (settings.includeScreenshot && message.wantsShot) {
      try {
        const shot = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
        const masked = await maskScreenshot(shot, message.maskRects || [], message.dpr || 1);
        if (masked) payload.screenshot_png_b64 = masked;
      } catch {
        // If masking is not certain to have worked, no screenshot is sent.
        delete payload.screenshot_png_b64;
      }
    }
    const stored = await api(settings, "/ext/v1/evidence", payload);
    const result = await api(settings, `/ext/v1/analyze/${stored.scan_id}`, {});
    return { ok: true, result };
  } catch (error) {
    return { ok: false, message: String(error && error.message ? error.message : error) };
  }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return false;
  if (message.type === "kmr:activate") {
    activate().then(reply, (e) => reply({ ok: false, message: String(e.message || e) }));
    return true;
  }
  if (message.type === "kmr:send") {
    send(message, sender).then(reply);
    return true;
  }
  if (message.type === "kmr:open") {
    getSettings().then((s) => {
      // Only KMR AI's own issue pages can be opened from a finding.
      if (s.webBase && /^\/issues\/[0-9a-f-]{36}$/.test(String(message.path))) {
        chrome.tabs.create({ url: s.webBase.replace(/\/$/, "") + message.path });
      }
      reply({ ok: true });
    });
    return true;
  }
  return false;
});
