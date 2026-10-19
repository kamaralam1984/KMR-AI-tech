# Live browser agent (Phase 13)

A Chrome extension (Manifest V3) that looks at **one tab you turn it on for**, highlights problems on
the page with boxes and a separate visible "KMR AI" cursor, and sends only **redacted** page data to
KMR AI for analysis. **This version only looks.** It does not click, type, submit or change the page.
"Fix this" opens the issue in the KMR AI web app, where the normal repair, check, diff-bound approval
and verification flow applies.

## Install (developer mode)

1. In KMR AI: Settings, **Browser extension**, create a token (shown once, up to 90 days, revocable).
2. Chrome: `chrome://extensions`, turn on Developer mode, **Load unpacked**, choose the `extension/` folder.
3. Extension options: server address (API), web address, token, **Load my projects**, choose a project.
   Only projects whose website you have **verified** are listed.
4. Open the site, click the extension icon, **Turn on for this tab**, then **Analyze this page**.

## What the four hard rules mean in practice

| Rule | How it is enforced | Where it is tested |
| --- | --- | --- |
| Never enters OTP, password, card or personal values; does not type at all | No code path for input, click, submit, focus or key events; overlay layers ignore the pointer; the cursor is only a drawing | `extension/test/agent.test.js` ("never types, clicks or submits": static scan of the sources plus a run that asserts zero events and unchanged field values) |
| Personal data does not leave the machine | Allow-list collection (never any input value), local redaction, optional blacked-out screenshot, then a second check on the server that rejects the **whole** payload if anything still looks personal | agent tests (fake personal data absent from the outgoing message), `backend/tests/test_extension_api.py` (10 leak types rejected, nothing stored, value never echoed) |
| No fix without an explicit user request | The extension has no repair/approval/merge call; "Fix this" only opens a KMR AI issue page. Analysis creates findings only | agent tests (background/content sources), existing approval tests |
| Fail closed on sensitive pages | Address block list + page content signals (password, OTP, card, ID fields, payment frames, "enter OTP" text). The background and the server each check the address again | agent tests (6 page types), `test_extension_api.py` (7 sensitive addresses), `test_extension_policy.py` |

## What is collected (allow-list)

- Page title, language, up to 8 headings, up to 5 visible alert texts, count of `h1`, viewport width, load time.
- Structural checks (images without alt, unlabeled fields, unnamed buttons/links, missing title/lang/viewport,
  heading structure, sideways overflow) with a **position-only CSS path** of the offending elements
  (tag and `nth-of-type`, or a short plain id). Never class names or attribute values.
- After you turn it on: console errors, uncaught errors, failed or 4xx/5xx fetch/XHR requests (addresses
  redacted). Errors from before you turned it on are **not** captured; reload and turn it on again to see them.
- Optional (off by default): a screenshot with all fields and personal-looking text blacked out. Skipped
  when the page has frames, video or canvas, or when masking fails.

Never collected: values of any field, cookies, storage, request or response bodies, headers.

"Show what will be sent" prints exactly the request body. With the default setting, analysis always
shows that preview and waits for **Send for analysis**.

## Server API

`/ext/v1`, bearer token, never cookies. Each call re-checks that the token's user still belongs to the
organization (member or above). Rate limits per token. Quota: an upload counts as one scan.

| Call | Purpose |
| --- | --- |
| `GET /ext/v1/projects` | Verified projects only |
| `POST /ext/v1/evidence` | Fail-closed ingest: sensitive page, wrong host, unverified project, unknown field or leftover personal data is rejected and nothing is stored |
| `POST /ext/v1/analyze/{scan_id}` | Normal diagnosis pipeline (quotas, labels, Needs Review). Only for scans this token's user uploaded |

Uploads become a scan with `limits.source = "extension"` and appear in the normal Issues list. Audit
events: `extension.token_create`, `extension.token_revoke`, `extension.ingest`, `extension.rejected`
(field paths and reasons only, never values).

## Redaction coverage and measured miss rate

Pattern-based. Covered: emails, phone numbers (10+ digits), card-like numbers (13-19 digits), 12-digit IDs
(Aadhaar-like), PAN, IFSC, JWTs, bearer/basic credentials, `key=value` secrets, long token-like strings,
codes after OTP/PIN/CVV, labelled details (`Name:`, `DOB:`, `Address:`, ...), honorific names (`Mr Firstname Lastname`),
and addresses (credentials, fragment and every query value removed, long ids in paths replaced).

**Not covered** (documented limits): names or addresses inside ordinary sentences, spelled-out numbers,
obfuscated emails (`john at example dot com`), usernames, bare postal codes.

Measured on `extension/test/redaction-cases.json` (60 cases labeled by the author, **not independently
reviewed**): all 38 cases labeled as covered are caught; the 10 cases labeled as known limits all still
leak; 12 ordinary strings are left unchanged. Counting the known limits, the miss rate on the labeled set
is **20.8%** (10 of 48). That set was written to match the patterns, so the real-world rate will be higher
than the "covered" figure suggests. Do not treat redaction as a guarantee: the block list, the field
allow-list, the preview and the server's second check exist because of this.

## Threat model (summary)

| Threat | Control |
| --- | --- |
| Page text tries to instruct the agent (prompt injection) | Page content is data only. The agent cannot act; the pipeline treats evidence as untrusted. Tested with an instruction in an alert |
| Page script reads or forges the overlay | Closed shadow root. Page-world hook events can be forged by the page, so they are labeled page-reported and only add observations |
| Broad browser permissions | No host permissions by default; `activeTab` after your click; the server address is requested as an optional permission when you load projects; no content scripts declared; no remote code; strict extension CSP |
| Token theft | Held only by the background service worker; hashed on the server; expires; revocable; re-checks membership each call |
| Screenshot shows data DOM redaction missed | Off by default; masks fields and personal-looking text; skipped when unsure |
| Page changes after the preview | The send step builds from the same code; the background rejects an address that differs from the tab's |
| Cross-tenant | Row-Level Security; a token only works for its own organization; analysis only for the uploader's own extension scans |

## How it was tested in a real browser

`make ext-e2e` drives Playwright's Chromium and `make ext-e2e-chrome` drives the installed Google Chrome
(154 when tested) against the running local API and a local test site: 28 checks, all passing in both.
They cover turning the agent on, the overlay, the highlight sitting exactly on the element, the cursor
moving to it, no click/input/submit events and no changed field values, the stored data holding no fake
personal data, the screenshot being blacked out where fields and personal-looking text are (pixel
checks), and `/login` and password pages being refused with nothing sent.

How the harness differs from a person using it (and nothing else): it grants the extension broad host
access in a COPY of the manifest because the toolbar click (`activeTab`) and the options-page permission
prompt cannot be clicked in an automated browser, and it opens the overlay's shadow root so the test can
read it. Google Chrome 137+ ignores `--load-extension`, so the Chrome run loads the extension with the
DevTools command `Extensions.loadUnpacked`, which is what "Load unpacked" does.

Not tested: a person clicking the toolbar icon and the permission prompt; headed (windowed) Chrome on
the development machine, which aborts at start-up with a GNOME settings-schema error unrelated to the
extension. `make ext-demo` sets up a test page so you can do that by hand.

## Known limits

- Chrome only, desktop, first version. Needs a manual load until it is published.
- A person using the toolbar icon and the permission prompt has not been tested (see above).
- Only verified-host pages of verified projects can be analyzed. Other sites get no cloud analysis.
- Console and network capture start when you turn the agent on.
