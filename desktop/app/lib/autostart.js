"use strict";

/** Start at login. Off unless the person turns it on. Linux uses an XDG autostart entry. */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function linuxFile() {
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return path.join(base, "autostart", "kmr-assistant.desktop");
}

function isEnabled(app) {
  if (process.platform === "linux") return fs.existsSync(linuxFile());
  return app.getLoginItemSettings().openAtLogin;
}

function setEnabled(app, enabled, execPath, args) {
  if (process.platform === "linux") {
    const file = linuxFile();
    if (!enabled) return fs.rmSync(file, { force: true });
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const exec = [execPath, ...args].map((a) => `"${a.replace(/"/g, '\\"')}"`).join(" ");
    fs.writeFileSync(
      file,
      `[Desktop Entry]\nType=Application\nName=KMR Assistant\nExec=${exec}\nX-GNOME-Autostart-enabled=true\n`,
    );
    return undefined;
  }
  app.setLoginItemSettings({ openAtLogin: enabled, args });
  return undefined;
}

module.exports = { isEnabled, setEnabled, linuxFile };
