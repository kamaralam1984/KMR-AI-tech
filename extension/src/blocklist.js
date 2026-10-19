/*
 * Sites and addresses the agent never reads. The server refuses the same pages
 * (backend/app/extension/policy.py); a test compares the two lists.
 * Users can ADD to this list in the options page. Nobody can remove from it.
 */
"use strict";

(function () {
  const blocklist = {
    hosts: [
      "paypal.com",
      "stripe.com",
      "razorpay.com",
      "paytm.com",
      "phonepe.com",
      "pay.google.com",
      "mail.google.com",
      "outlook.live.com",
      "outlook.office.com",
      "outlook.office365.com",
      "mail.yahoo.com",
      "mail.proton.me",
      "accounts.google.com",
      "login.microsoftonline.com",
      "login.live.com",
      "appleid.apple.com",
      "hdfcbank.com",
      "onlinesbi.sbi",
      "sbi.co.in",
      "icicibank.com",
      "axisbank.com",
      "kotak.com",
    ],
    hostPatterns: ["bank", "\\.gov(\\.[a-z]{2})?$", "\\.nic\\.in$", "\\.mil$"],
    pathPattern:
      "(^|/)(log-?in|sign-?in|signin|sso|auth|oauth|otp|verify|2fa|mfa|password|passwd|reset|" +
      "forgot|pay|payment|payments|checkout|billing|card|wallet|netbanking|banking)(/|$|[-_.])",
  };

  if (typeof module !== "undefined" && module.exports)
    module.exports = blocklist;
  else (globalThis.KMR_EXT = globalThis.KMR_EXT || {}).blocklist = blocklist;
})();
