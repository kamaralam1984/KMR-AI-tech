"use strict";

const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { AgentProcess } = require("./lib/agent_process");
const autostart = require("./lib/autostart");

const agent = new AgentProcess(__dirname);
let win = null;
let tray = null;
let quitting = false;

function prefsFile() {
  return path.join(app.getPath("userData"), "prefs.json");
}
function readPrefs() {
  try {
    return JSON.parse(fs.readFileSync(prefsFile(), "utf8"));
  } catch {
    return {};
  }
}
function writePrefs(next) {
  fs.mkdirSync(path.dirname(prefsFile()), { recursive: true });
  fs.writeFileSync(prefsFile(), JSON.stringify(next));
}

function createWindow() {
  win = new BrowserWindow({
    width: 430,
    height: 760,
    minWidth: 360,
    minHeight: 520,
    title: "KMR Assistant",
    icon: path.join(__dirname, "assets", "icon.png"),
    backgroundColor: "#0b0d12",
    show: process.env.KMR_APP_HIDDEN !== "1",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  // The window can show nothing but our own page: no navigation, no pop-ups.
  win.webContents.on("will-navigate", (event) => event.preventDefault());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.on("close", (event) => {
    // Closing the window keeps the assistant running, so it is still there when the browser is gone.
    if (!quitting && readPrefs().background !== false) {
      event.preventDefault();
      win.hide();
    }
  });
  win.on("closed", () => {
    win = null;
  });
}

function showWindow() {
  if (!win) createWindow();
  win.show();
  win.focus();
}

function createTray() {
  try {
    const icon = nativeImage.createFromPath(path.join(__dirname, "assets", "tray.png"));
    tray = new Tray(icon);
    tray.setToolTip("KMR Assistant");
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: "Open KMR Assistant", click: showWindow },
        {
          label: "Quit",
          click: () => {
            quitting = true;
            app.quit();
          },
        },
      ]),
    );
    tray.on("click", showWindow);
  } catch {
    tray = null; // some desktops have no tray; launching the app again opens the window
  }
}

app.on("second-instance", showWindow);
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

ipcMain.handle("agent", (_event, { method, path: requestPath, body }) =>
  agent.call(String(method), String(requestPath), body),
);
ipcMain.handle("app:prefs", () => ({
  ...readPrefs(),
  autostart: autostart.isEnabled(app),
  platform: process.platform,
}));
ipcMain.handle("app:set-pref", (_event, key, value) => {
  if (!["theme", "background", "lang"].includes(key)) return false;
  writePrefs({ ...readPrefs(), [key]: value });
  return true;
});
ipcMain.handle("app:set-autostart", (_event, enabled) => {
  autostart.setEnabled(app, !!enabled, process.execPath, [app.getAppPath()]);
  return autostart.isEnabled(app);
});
ipcMain.handle("app:quit", () => {
  quitting = true;
  app.quit();
});
ipcMain.handle("app:hide", () => win && win.hide());

app.whenReady().then(async () => {
  try {
    await agent.start();
  } catch (error) {
    console.error("agent:", error.message);
  }
  createWindow();
  createTray();
});
app.on("before-quit", () => {
  quitting = true;
  agent.stop();
});
app.on("window-all-closed", () => {
  if (quitting) app.quit();
});
