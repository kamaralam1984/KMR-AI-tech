/*
 * Runs in the PAGE's own world, only after the user turned the agent on for this tab. It watches
 * (it does not change behaviour): console errors, uncaught errors, and fetch/XHR failures that
 * happen from now on. Events go to the extension through a window event. The page can see and
 * forge those events, so everything it reports is treated as untrusted page-reported data.
 */
(function () {
  "use strict";
  if (window.__kmrAgentHook) return;
  Object.defineProperty(window, "__kmrAgentHook", { value: true, enumerable: false });
  let sent = 0;
  const emit = (payload) => {
    if (sent >= 60) return;
    sent += 1;
    try {
      window.dispatchEvent(new CustomEvent("kmr-agent-log", { detail: JSON.stringify(payload) }));
    } catch (e) {
      /* never break the page */
    }
  };
  const text = (list) =>
    Array.prototype.map
      .call(list, (v) => (v && v.message ? v.message : String(v)))
      .join(" ")
      .slice(0, 500);

  for (const level of ["error", "warn"]) {
    const original = console[level];
    console[level] = function () {
      emit({ type: "console", level, message: text(arguments) });
      return original.apply(this, arguments);
    };
  }
  window.addEventListener("error", (e) => emit({ type: "page_error", message: e.message || "Error" }));
  window.addEventListener("unhandledrejection", (e) =>
    emit({ type: "page_error", message: "Unhandled promise rejection: " + text([e.reason]) }),
  );

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : (input && input.url) || String(input);
      const method = (init && init.method) || (input && input.method) || "GET";
      return originalFetch.apply(this, arguments).then(
        (response) => {
          if (!response.ok) emit({ type: "http", url, status: response.status, resource_type: "fetch" });
          return response;
        },
        (error) => {
          emit({ type: "failed", url, method, failure: "network error" });
          throw error;
        },
      );
    };
  }
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.addEventListener("loadend", () => {
      if (this.status >= 400) emit({ type: "http", url: String(url), status: this.status, resource_type: "xhr" });
      else if (this.status === 0) emit({ type: "failed", url: String(url), method, failure: "network error" });
    });
    return open.apply(this, arguments);
  };
})();
