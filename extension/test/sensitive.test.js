import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const sensitive = require("../src/sensitive.js");

function page(html, url = "https://shop.example.com/about") {
  document.body.innerHTML = html;
  return sensitive.assessPage(document, url, []);
}

describe("addresses that are never read", () => {
  it.each([
    "https://shop.example.com/login",
    "https://shop.example.com/user/sign-in",
    "https://shop.example.com/checkout/step-2",
    "https://shop.example.com/account/verify-otp",
    "https://mail.google.com/mail/u/0/",
    "https://www.paypal.com/signin",
    "https://netbanking.hdfcbank.com/",
    "https://www.somebank.co.in/home",
    "https://portal.example.gov.in/apply",
    "https://income.nic.in/",
    "chrome://settings",
    "file:///etc/passwd",
  ])("blocks %s", (url) => {
    expect(sensitive.addressReason(url)).not.toBeNull();
  });

  it("allows an ordinary page and honours the user's own additions", () => {
    expect(sensitive.addressReason("https://shop.example.com/about")).toBeNull();
    expect(sensitive.addressReason("https://shop.example.com/about", ["example.com"])).not.toBeNull();
    expect(sensitive.addressReason("https://shop.example.com/about", ["other.org"])).toBeNull();
  });
});

describe("page content that rules a page out", () => {
  it("reads a harmless page", () => {
    expect(page("<h1>About us</h1><p>We sell shoes.</p>").blocked).toBe(false);
  });
  it.each([
    ['<input type="password" name="p">', "password"],
    ['<input name="otp" type="text">', "code"],
    ['<input autocomplete="one-time-code">', "code"],
    ['<input autocomplete="cc-number">', "card"],
    ['<input placeholder="Card number">', "card"],
    ['<input aria-label="Aadhaar number">', "ID"],
    ["<p>Please enter the OTP sent to your phone</p>", "code"],
    ['<iframe src="https://js.stripe.com/v3/elements"></iframe>', "payment"],
  ])("blocks a page with %s", (html) => {
    const verdict = page(html);
    expect(verdict.blocked).toBe(true);
    expect(verdict.reason).toBeTruthy();
  });
});
