"use strict";

/* Pure helpers for the chat window, kept apart so they can be tested without a browser. */
(function () {
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, k) => (k in values ? String(values[k]) : ""));

  function statusLine(status, strings) {
    if (!status || !status.connected) return strings.notConnected;
    const label = status.provider_label || "";
    return label ? `${strings.connected} · ${label}` : strings.connected;
  }

  function disclaimer(status, strings) {
    return status && status.connected && status.is_ai === false ? strings.disclaimerRules : strings.disclaimerAi;
  }

  function stepTitle(step, strings) {
    const verb = { highlight: strings.stepHighlight, press: strings.stepPress, type: strings.stepType }[step.kind] || step.kind;
    const what = step.name ? `“${step.name}”` : `(${step.role})`;
    return `${verb} ${what}`;
  }

  function secondsLeft(expiresAtMs, nowMs) {
    return Math.max(0, Math.ceil((expiresAtMs - nowMs) / 1000));
  }

  function clock(seconds) {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function describeWhere(info, windowTitle) {
    const parts = [info.role ? `a ${info.role.replace("_", " ")}` : "an element"];
    if (info.name) parts[0] += ` named “${info.name}”`;
    if (windowTitle) parts.push(`in “${windowTitle}”`);
    if (info.bounds && info.bounds[2] > 0 && info.bounds[3] > 0 && (info.bounds[0] > 0 || info.bounds[1] > 0)) {
      parts.push(`around x ${info.bounds[0]}, y ${info.bounds[1]}`);
    }
    return parts.join(" ");
  }

  function canSend(text, working) {
    return !working && text.trim().length > 0 && text.length <= 2000;
  }

  const api = { fill, statusLine, disclaimer, stepTitle, secondsLeft, clock, describeWhere, canSend };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.KMR_LOGIC = api;
})();
