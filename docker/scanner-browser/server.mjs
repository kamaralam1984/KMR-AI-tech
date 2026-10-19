// Starts one Chromium and exposes it to the worker over a WebSocket. All launch options live
// here; clients can open contexts and pages but cannot change the proxy or the flags.
import { chromium, firefox } from "playwright";

const proxyUrl = process.env.PROXY_URL;
const wsPath = process.env.SCAN_WS_PATH;
if (!proxyUrl || !wsPath || wsPath.length < 16) {
  console.error("PROXY_URL and a SCAN_WS_PATH of at least 16 characters are required");
  process.exit(1);
}
const proxyHost = new URL(proxyUrl).hostname;

const server = await chromium.launchServer({
  host: "0.0.0.0",
  port: 3000,
  wsPath,
  headless: true,
  // The browser resolves no names itself (the proxy does), except the proxy's own address, and
  // loopback is NOT exempt from the proxy, so a page cannot reach services in this container.
  proxy: { server: proxyUrl, bypass: "<-loopback>" },
  args: [
    `--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE ${proxyHost}`,
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-first-run",
    "--mute-audio",
    "--disable-dev-shm-usage",
  ],
});
const servers = [server];
console.log(JSON.stringify({ service: "scanner-browser", engine: "chromium", event: "listening", path: "(secret)" }));

// Firefox is optional: it starts only if its own secret path is set. Same rules as Chromium: every
// request, loopback included, goes through the egress proxy, and the browser resolves no names.
// WebKit is deliberately NOT offered: it has not been verified against the proxy checks.
const firefoxPath = process.env.SCAN_WS_PATH_FIREFOX;
if (firefoxPath) {
  if (firefoxPath.length < 16) {
    console.error("SCAN_WS_PATH_FIREFOX must be at least 16 characters");
    process.exit(1);
  }
  servers.push(
    await firefox.launchServer({
      host: "0.0.0.0",
      port: 3001,
      wsPath: firefoxPath,
      headless: true,
      proxy: { server: proxyUrl },
      // Firefox's own process sandbox cannot create user namespaces in this locked-down container
      // (no capabilities, default seccomp), so it logs a warning and runs without it, like the
      // Chromium here. The container (read-only filesystem, no route except the proxy) is the sandbox.
      firefoxUserPrefs: {
        "network.proxy.allow_hijacking_localhost": true,
        "network.proxy.no_proxies_on": "",
        "network.proxy.failover_timeout": 0,
        "network.dns.disablePrefetch": true,
        "network.dns.disablePrefetchFromHTTPS": true,
        "network.prefetch-next": false,
        "network.predictor.enabled": false,
        "network.http.speculative-parallel-limit": 0,
        "network.trr.mode": 5,
        "media.peerconnection.enabled": false,
        "browser.safebrowsing.enabled": false,
        "extensions.autoDisableScopes": 15,
      },
    }),
  );
  console.log(JSON.stringify({ service: "scanner-browser", engine: "firefox", event: "listening", path: "(secret)" }));
}
const stop = async () => { await Promise.all(servers.map((s) => s.close())); process.exit(0); };
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
