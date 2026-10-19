"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// The chat window gets exactly these functions. It never receives the agent secret or any token.
contextBridge.exposeInMainWorld("kmr", {
  agent: (method, path, body) => ipcRenderer.invoke("agent", { method, path, body }),
  prefs: () => ipcRenderer.invoke("app:prefs"),
  setPref: (key, value) => ipcRenderer.invoke("app:set-pref", key, value),
  setAutostart: (enabled) => ipcRenderer.invoke("app:set-autostart", enabled),
  quit: () => ipcRenderer.invoke("app:quit"),
  hide: () => ipcRenderer.invoke("app:hide"),
});
