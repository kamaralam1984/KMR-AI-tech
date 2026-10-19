"use strict";

// `pnpm start`: runs Electron. On Linux a development checkout has no SUID sandbox helper, so it is run
// with --no-sandbox; a packaged install must set that helper up instead. Windows needs no such flag.
const { spawn } = require("node:child_process");
const electron = require("electron");

const fs = require("node:fs");
const path = require("node:path");

// Electron's sandbox needs a root-owned setuid helper. If it is set up, use the sandbox; if not (a plain
// checkout), start with --no-sandbox and say so.
function sandboxReady() {
  try {
    const helper = path.join(path.dirname(electron), "chrome-sandbox");
    const info = fs.statSync(helper);
    return info.uid === 0 && (info.mode & 0o4000) !== 0;
  } catch {
    return false;
  }
}

const args = ["."];
if (process.platform === "linux" && !sandboxReady()) {
  args.push("--no-sandbox");
  console.error("Note: Electron's sandbox helper is not set up, starting with --no-sandbox (see desktop/UBUNTU.md).");
}
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, args, { stdio: "inherit", env });
child.on("exit", (code) => process.exit(code ?? 0));
