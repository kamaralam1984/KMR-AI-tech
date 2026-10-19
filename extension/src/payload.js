/*
 * The one place the outgoing request body is built. "Show what will be sent" prints exactly this,
 * and sending uses exactly this, so the preview cannot drift from the real request.
 */
"use strict";

(function () {
  const dep = (name, path) =>
    typeof require === "function" ? require(path) : globalThis.KMR_EXT[name];
  const redact = dep("redact", "./redact.js");

  const CLIENT_VERSION = "0.1.0";

  function buildPayload(
    collected,
    { projectId, pageUrl, hasScreenshot = false },
  ) {
    return {
      project_id: projectId,
      page_url: pageUrl,
      client: {
        version: CLIENT_VERSION,
        sensitive_page: false,
        redaction_version: redact.VERSION,
        redacted_counts: collected.counts,
        masked_regions: hasScreenshot ? collected.maskRects.length : 0,
      },
      observations: collected.observations,
    };
  }

  function preview(payload, screenshotIncluded) {
    const text = JSON.stringify(payload, null, 2);
    return screenshotIncluded
      ? `${text}\n\n// A screenshot is also sent. Fields, passwords and text that looks personal are blacked out first.`
      : `${text}\n\n// No screenshot is sent.`;
  }

  const api = { buildPayload, preview, CLIENT_VERSION };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).payload = api;
})();
