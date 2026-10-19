"use strict";
const $ = (id) => document.getElementById(id);
const status = (text) => ($("status").textContent = text);

async function tab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}
function enable(on) {
  for (const id of ["analyze", "preview", "off"]) $(id).disabled = !on;
}

$("on").addEventListener("click", async () => {
  status("Turning on...");
  const answer = await chrome.runtime.sendMessage({ type: "kmr:activate" });
  if (!answer || !answer.ok) return status(answer ? answer.message : "Could not turn on.");
  if (answer.blocked) {
    enable(false);
    return status(`Not reading this page: ${answer.reason}. Nothing is collected or sent.`);
  }
  enable(true);
  status("On for this tab. Use “Analyze this page” when you are ready.");
});

async function settings() {
  return chrome.storage.local.get(["projectId"]);
}

$("preview").addEventListener("click", async () => {
  const t = await tab();
  const { projectId } = await settings();
  const answer = await chrome.tabs.sendMessage(t.id, { type: "kmr:preview", projectId: projectId || "" });
  const box = $("preview-text");
  box.hidden = false;
  box.textContent = answer.blocked ? `Not reading this page: ${answer.reason}` : answer.text;
});

$("analyze").addEventListener("click", async () => {
  const t = await tab();
  const { projectId } = await settings();
  if (!projectId) return status("Choose a project in Settings first.");
  await chrome.tabs.sendMessage(t.id, { type: "kmr:analyze", projectId });
  status("Check the panel on the page.");
  window.close();
});

$("off").addEventListener("click", async () => {
  const t = await tab();
  await chrome.tabs.sendMessage(t.id, { type: "kmr:off" });
  enable(false);
  status("Off for this tab.");
});
