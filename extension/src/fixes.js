/*
 * "Preview fix": the only thing the agent ever changes on a page, and only after the user allows it
 * for ONE element. It edits a single attribute (or the page title) in this tab's copy of the page.
 * Nothing is sent anywhere, the real website is unchanged, a reload undoes it, and every change can
 * be undone from the panel.
 *
 * It never types into fields, never clicks, never submits, and never invents the text: the user
 * writes the label or description in the agent's own panel. That text is kept local.
 */
"use strict";

(function () {
  const MAX_TEXT = 120;
  const LANG = /^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$/;

  /** What each supported check can do. Anything not listed gets no preview button. */
  const FIXES = {
    images_missing_alt: {
      what: "give this image a text description (alt text)",
      attribute: "alt",
      prompt: "Describe the image (leave empty if it is only decoration)",
      required: false,
    },
    inputs_without_label: {
      what: "give this field an accessible name",
      attribute: "aria-label",
      prompt: "What should this field be called?",
      required: true,
    },
    buttons_without_name: {
      what: "give this button an accessible name",
      attribute: "aria-label",
      prompt: "What does this button do?",
      required: true,
    },
    links_without_name: {
      what: "give this link an accessible name",
      attribute: "aria-label",
      prompt: "Where does this link go?",
      required: true,
    },
    missing_lang: {
      what: "declare the page language",
      attribute: "lang",
      prompt: "Language code (for example en, hi or fr)",
      required: true,
      lang: true,
    },
    missing_title: {
      what: "give the page a title",
      title: true,
      prompt: "Page title",
      required: true,
    },
  };

  function supports(check) {
    return Object.prototype.hasOwnProperty.call(FIXES, check);
  }

  function clean(raw) {
    return String(raw == null ? "" : raw)
      .replace(/[\u0000-\u001f\u007f<>]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, MAX_TEXT);
  }

  /** { ok, value, message }. The user's text is checked here, never trusted from anywhere else. */
  function validate(check, raw) {
    const spec = FIXES[check];
    if (!spec)
      return {
        ok: false,
        value: "",
        message: "There is no preview for this problem.",
      };
    const value = clean(raw);
    if (spec.required && !value)
      return { ok: false, value, message: "Please write something." };
    if (spec.lang && !LANG.test(value)) {
      return {
        ok: false,
        value,
        message: "Use a language code such as en or hi.",
      };
    }
    return { ok: true, value, message: "" };
  }

  /** The sentence shown to the user before they allow it. Says exactly what will change. */
  function describe(check, value, selector) {
    const spec = FIXES[check];
    if (spec.title) return `Set the page title to “${value}” in this tab.`;
    return `Set ${spec.attribute}="${value}" on ${selector} to ${spec.what}, in this tab only.`;
  }

  /**
   * Applies the fix and returns { undo, verified, detail }. `verified` is a local re-check of the
   * same condition the scan flagged; it says nothing about the real website.
   */
  function apply(doc, el, check, value) {
    const spec = FIXES[check];
    if (spec.title) {
      const before = doc.title;
      doc.title = value;
      return {
        undo: () => {
          doc.title = before;
        },
        verified: !!doc.title,
        detail: "The page now has a title.",
      };
    }
    const had = el.hasAttribute(spec.attribute);
    const before = el.getAttribute(spec.attribute);
    el.setAttribute(spec.attribute, value);
    const verified =
      check === "images_missing_alt"
        ? el.hasAttribute("alt")
        : (el.getAttribute(spec.attribute) || "").trim().length > 0;
    return {
      undo: () => {
        if (had) el.setAttribute(spec.attribute, before);
        else el.removeAttribute(spec.attribute);
      },
      verified,
      detail: verified
        ? "Checked again in this tab: the problem is gone here."
        : "Applied, but the check still fails here.",
    };
  }

  const api = { FIXES, supports, validate, describe, apply, MAX_TEXT };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).fixes = api;
})();
