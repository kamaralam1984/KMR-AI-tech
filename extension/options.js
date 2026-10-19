"use strict";
const $ = (id) => document.getElementById(id);
const say = (text) => ($("msg").textContent = text);
const KEYS = ["apiBase", "webBase", "token", "projectId", "userBlocklist", "confirmBeforeSend", "includeScreenshot"];

function setProjects(list, selected) {
  const select = $("projectId");
  select.replaceChildren();
  for (const p of list) {
    const option = document.createElement("option");
    option.value = p.id;
    option.textContent = `${p.name} (${p.site_host})`;
    option.selected = p.id === selected;
    select.append(option);
  }
}

async function ask(origin) {
  // The server address is requested as a host permission only when you save it.
  const url = new URL(origin);
  return chrome.permissions.request({ origins: [`${url.origin}/*`] });
}

$("load").addEventListener("click", async () => {
  try {
    const base = $("apiBase").value.trim().replace(/\/$/, "");
    if (!base || !$("token").value) return say("Enter the server address and token first.");
    if (!(await ask(base))) return say("Permission to contact that server was not granted.");
    const response = await fetch(`${base}/ext/v1/projects`, {
      headers: { Authorization: `Bearer ${$("token").value.trim()}` },
      credentials: "omit",
    });
    if (!response.ok) return say(`The server answered ${response.status}. Check the token.`);
    const list = await response.json();
    setProjects(list, $("projectId").value);
    say(list.length ? `${list.length} verified project(s) found.` : "No verified projects. Verify a website in KMR AI first.");
  } catch (error) {
    say(`Could not reach the server: ${error.message}`);
  }
});

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    apiBase: $("apiBase").value.trim().replace(/\/$/, ""),
    webBase: $("webBase").value.trim().replace(/\/$/, ""),
    token: $("token").value.trim(),
    projectId: $("projectId").value,
    userBlocklist: $("blocklist").value.split(/\s+/).filter(Boolean),
    confirmBeforeSend: $("confirm").checked,
    includeScreenshot: $("shot").checked,
  });
  say("Saved.");
});

(async () => {
  const s = await chrome.storage.local.get(KEYS);
  $("apiBase").value = s.apiBase || "";
  $("webBase").value = s.webBase || "";
  $("token").value = s.token || "";
  $("blocklist").value = (s.userBlocklist || []).join("\n");
  $("confirm").checked = s.confirmBeforeSend !== false;
  $("shot").checked = !!s.includeScreenshot;
  if (s.projectId) setProjects([{ id: s.projectId, name: "Saved project", site_host: "" }], s.projectId);
})();
