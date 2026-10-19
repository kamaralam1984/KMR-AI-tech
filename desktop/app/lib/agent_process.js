"use strict";

/**
 * Starts the Python agent and talks to its local API. The per-run secret lives here, in the main
 * process only: the chat window never sees it, so a compromised page cannot approve a step.
 */
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");

// The only calls the chat window may make. Anything else is refused here, before the agent.
const ALLOWED = [
  ["GET", /^\/status$/],
  ["GET", /^\/windows$/],
  ["POST", /^\/prepare$/],
  ["POST", /^\/send$/],
  ["POST", /^\/config$/],
  ["POST", /^\/steps\/[0-9a-f-]{36}\/(approve|reject|highlight)$/],
];

function allowed(method, p) {
  return ALLOWED.some(([m, re]) => m === method && re.test(p));
}

function agentCommand(appDir) {
  if (process.env.KMR_AGENT_PYTHON) return process.env.KMR_AGENT_PYTHON;
  const agentDir = path.join(appDir, "..", "agent");
  const venv =
    process.platform === "win32"
      ? path.join(agentDir, ".venv", "Scripts", "python.exe")
      : path.join(agentDir, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : process.platform === "win32" ? "python" : "/usr/bin/python3";
}

class AgentProcess {
  constructor(appDir) {
    this.appDir = appDir;
    this.secret = crypto.randomBytes(32).toString("hex");
    this.port = null;
    this.child = null;
    this.stopped = false;
    this.restarts = 0;
  }

  start() {
    return new Promise((resolve, reject) => {
      const agentDir = path.join(this.appDir, "..", "agent");
      const env = { ...process.env, KMR_LOCAL_SECRET: this.secret, PYTHONPATH: agentDir };
      // A desktop app started from an editor can inherit variables that make GTK libraries abort.
      for (const k of ["GSETTINGS_SCHEMA_DIR", "GTK_PATH", "GTK_EXE_PREFIX", "GIO_MODULE_DIR"]) delete env[k];
      this.child = spawn(agentCommand(this.appDir), ["-m", "kmr_desktop", "run"], {
        cwd: agentDir,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let buffer = "";
      let settled = false;
      const timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          reject(new Error("The agent did not start in time."));
        }
      }, 15000);
      this.child.stdout.on("data", (chunk) => {
        buffer += chunk.toString();
        const line = buffer.split("\n").find((l) => l.startsWith("{"));
        if (line && !settled) {
          try {
            const ready = JSON.parse(line);
            if (ready.event === "ready") {
              this.port = ready.port;
              settled = true;
              clearTimeout(timer);
              resolve(this.port);
            }
          } catch {
            /* keep reading */
          }
        }
      });
      this.child.stderr.on("data", () => {}); // never log: it may hold what the person typed
      this.child.on("exit", () => {
        clearTimeout(timer);
        if (!settled) {
          settled = true;
          reject(new Error("The agent stopped at start-up."));
        }
        this.port = null;
        if (!this.stopped && this.restarts < 3) {
          this.restarts += 1;
          setTimeout(() => this.start().catch(() => {}), 1000 * this.restarts);
        }
      });
    });
  }

  stop() {
    this.stopped = true;
    if (this.child) this.child.kill();
  }

  call(method, requestPath, body) {
    return new Promise((resolve) => {
      if (!allowed(method, requestPath)) {
        return resolve({ status: 400, body: { error: "That call is not allowed." } });
      }
      if (!this.port) return resolve({ status: 503, body: { error: "The agent is not running." } });
      const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
      const request = http.request(
        {
          host: "127.0.0.1",
          port: this.port,
          path: requestPath,
          method,
          headers: {
            "X-KMR-Local": this.secret,
            Host: `127.0.0.1:${this.port}`,
            ...(data ? { "Content-Type": "application/json", "Content-Length": data.length } : {}),
          },
          timeout: 120000,
        },
        (response) => {
          const chunks = [];
          response.on("data", (c) => chunks.push(c));
          response.on("end", () => {
            let parsed = {};
            try {
              parsed = JSON.parse(Buffer.concat(chunks).toString() || "{}");
            } catch {
              parsed = { error: "The agent sent something unreadable." };
            }
            resolve({ status: response.statusCode, body: parsed });
          });
        },
      );
      request.on("error", () => resolve({ status: 503, body: { error: "The agent is not reachable." } }));
      request.on("timeout", () => {
        request.destroy();
        resolve({ status: 504, body: { error: "The agent took too long." } });
      });
      if (data) request.write(data);
      request.end();
    });
  }
}

module.exports = { AgentProcess, allowed };
