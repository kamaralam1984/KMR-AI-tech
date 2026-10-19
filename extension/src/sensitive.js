/*
 * Decides whether a page is too sensitive to read. Fail closed: if any signal says "sensitive",
 * nothing is collected and nothing is sent.
 */
"use strict";

(function () {

const blocklist =
  typeof require === "function" ? require("./blocklist.js") : globalThis.KMR_EXT.blocklist;

const SENSITIVE_FIELD =
  /otp|one.?time|passcode|\bpin\b|cvv|cvc|card.?number|cc-?num|iban|account.?number|ifsc|aadhaar|aadhar|\bpan\b|\bssn\b/i;
const SENSITIVE_AUTOCOMPLETE =
  /\b(one-time-code|cc-number|cc-csc|cc-exp|cc-name|current-password|new-password)\b/;
const SENSITIVE_TEXT = /enter (the |your )?(otp|one.?time (password|code)|verification code|pin)/i;
const PAYMENT_FRAME = /(stripe\.com|razorpay\.com|paypal\.com|paytm\.com|checkout\.|pay\.)/i;

function matchHost(host, list) {
  return list.some((h) => host === h || host.endsWith("." + h));
}

/** Reason the ADDRESS alone rules a page out, or null. */
function addressReason(url, userBlocklist = []) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "unreadable address";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "not a web page";
  const host = parsed.hostname.toLowerCase();
  if (matchHost(host, blocklist.hosts)) {
    return "site is on the default block list (banking, mail, payment or sign-in provider)";
  }
  if (blocklist.hostPatterns.some((p) => new RegExp(p, "i").test(host))) {
    return "site looks like a bank or government service";
  }
  if (new RegExp(blocklist.pathPattern, "i").test(parsed.pathname)) {
    return "the address looks like a sign-in, verification, payment or checkout page";
  }
  if (matchHost(host, userBlocklist.map((h) => String(h).toLowerCase()))) {
    return "site is on your own block list";
  }
  return null;
}

/** Reason the PAGE CONTENT rules it out (password, OTP, card fields...), or null. */
function contentReason(doc) {
  if (doc.querySelector('input[type="password"]')) return "the page has a password field";
  for (const el of doc.querySelectorAll("input, textarea, select")) {
    const auto = el.getAttribute("autocomplete") || "";
    if (SENSITIVE_AUTOCOMPLETE.test(auto)) return "the page asks for a code, card or password";
    const hint = [
      el.getAttribute("name"),
      el.getAttribute("id"),
      el.getAttribute("placeholder"),
      el.getAttribute("aria-label"),
    ]
      .filter(Boolean)
      .join(" ");
    if (SENSITIVE_FIELD.test(hint)) return "the page asks for a code, card, bank or ID number";
  }
  for (const frame of doc.querySelectorAll("iframe[src]")) {
    if (PAYMENT_FRAME.test(frame.getAttribute("src") || "")) return "the page embeds a payment form";
  }
  const body = doc.body;
  const text = (body && (body.innerText ?? body.textContent)) || "";
  if (SENSITIVE_TEXT.test(text.slice(0, 20000))) return "the page asks you to enter a code or PIN";
  return null;
}

/** { blocked, reason } for a page. Checks the address first, then the content. */
function assessPage(doc, url, userBlocklist = []) {
  const byAddress = addressReason(url, userBlocklist);
  if (byAddress) return { blocked: true, reason: byAddress };
  const byContent = contentReason(doc);
  if (byContent) return { blocked: true, reason: byContent };
  return { blocked: false, reason: null };
}

const api = { addressReason, contentReason, assessPage };
if (typeof module !== "undefined" && module.exports) module.exports = api;
else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).sensitive = api;
})();
