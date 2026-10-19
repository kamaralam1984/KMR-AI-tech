# Owner-side scanner enhancements (Phase 12)

All three keep the read-only scan rules, the evidence labels and the Phase 3 limits.

## 12A Business-impact ranking (a labeled heuristic)

`GET /v1/organizations/{org}/issues/ranked` and **Issues, Order: Likely business impact**.

Score = severity weight (1-5) x journey weight (1-5). The journey (checkout/payment 5, login 4,
sign-up 4, application or booking form 4, account 3, contact 3, home 3, search/catalogue 2, other 1)
is guessed **only from the page address** of the evidence. Every result says `basis: heuristic`,
`measured: false` and lists its reasons. No traffic, revenue, conversion or user-loss number is
produced or shown. Analytics integration is **not built**; until it is, impact is never "measured".
Tests: `backend/tests/test_phase12.py`.

### Customer-supplied traffic (built)

Project page, **Traffic (optional)**: upload a CSV exported from your own analytics (a path column
and a views column; period and a label for where it came from). The ranking then multiplies the
score by a factor from 1.0 (page not in the data) to 3.0 (page with 20% or more of all imported
views), shows the page's share, and labels the basis `heuristic+customer_traffic` with
`source: customer_supplied`. KMR AI does **not** measure, verify or enrich these numbers, does not
connect to Google Analytics or any analytics service, reads page views only (other columns are ignored),
and calculates no revenue, conversion or user-loss figure. Uploads replace the previous data, are
tenant-scoped, audited (`traffic.import`) and can be deleted. Tests: `backend/tests/test_traffic_and_github.py`.

## 12B Developer-ready ticket

`GET /v1/organizations/{org}/issues/{id}/ticket` returns Markdown built from the stored finding
(observed / inferred / unverified kept apart, repro steps, evidence IDs, confidence, heuristic impact
labeled). Free text is redacted with the scan redactor; screenshots are referenced by ID, never embedded.
The page shows the text first; **Copy, Download .md, Email draft (a mailto link you click) and Print or
save as PDF** are explicit clicks. The preview is audited (`issue.ticket_preview`). Masking is
pattern-based and can miss things; the page says so.

### Send to GitHub (built, off by default)

`POST .../issues/{id}/github-issue` creates one GitHub issue from the previewed ticket. It is an
outward action, so:

- it is **off** unless `KMR_GITHUB_ISSUES_ENABLED=true`, and the GitHub App or token needs the
  **Issues: Read and write** permission, which the repair connection does not otherwise need. Without
  it GitHub refuses and the user is told exactly that;
- only an **admin** can do it, and the request must carry `confirm: true` and the SHA-256 of the exact
  ticket text that was previewed. If the text changed since (new evidence, new traffic), it is refused
  and the user previews again, so what is sent is what was shown;
- a finding is sent **at most once** (unique record); the URL, repository and text hash are audited
  (`issue.github_created`), never the text or the token;
- only a title and body are sent: no labels, assignees or milestones; the repository is never written to.

**Not verified against real GitHub.** Like the repair adapter, it is tested against a stateful fake
only (`FakeGitHub`), because no credentials were available. Try it on a throwaway repository first.

## 12C Device and slow-network profiles

`POST .../projects/{id}/scans?profile=<key>` with `desktop` (default), `phone` (360x640 touch),
`slow_network` (about 1.6 Mbit/s, 150 ms latency, pages get 2x load time), `low_end_phone` (phone, CPU 4x
slower, 3G-like, 3x load time). The exact conditions are stored with the scan (`limits.profile`) so a scan
can be repeated. A slower profile counts as 2 or 3 scans of the daily quota. Page, request, byte and
overall time limits are unchanged.

Findings that appear only under a profile: `GET .../scans/{a}/compare/{b}` (same project, completed scans)
lists findings only in one, found in both, and measured load times. Load times are **measured in the scan
environment under that throttling**, not real-visitor numbers. A new check, `horizontal_overflow`
(page wider than the screen), is what typically shows up only on the phone profile.

**Other browser engines.** `?engine=chromium|firefox|webkit` and `KMR_SCAN_ENGINES_AVAILABLE` (default
`["chromium"]`). An engine is listed only after it passes the scanner's whole security suite on that
deployment:

- **Firefox: built and verified.** `backend/tests/test_scanner.py` runs the entire scanner test suite
  (redirects, forms, popups, downloads, limits, request IDs, SSRF blocks) for both engines, plus two
  engine-specific tests: loopback is not exempt from the proxy, and names are resolved only by the
  proxy (a name only the proxy's resolver knows still loads). `make scanner-check` repeats the real
  sandbox checks against the running containers for every listed engine; Firefox passed all of them.
  To enable: set `KMR_SCAN_BROWSER_PATH_FIREFOX` (a long random path), run `make scanner-up`, set
  `KMR_SCAN_BROWSER_WS_FIREFOX` and `KMR_SCAN_ENGINES_AVAILABLE=["chromium","firefox"]`. Startup refuses a
  listed engine that has no browser configured.
- **WebKit: not available.** The code path exists but was never verified: WebKit could not even start on
  the development machine (a system library, `libevent-2.1.so.7`, is missing and the installed build does
  not match the Playwright version). The container does not offer it and the API refuses it.
- Slow-network and low-end-phone profiles need Chrome DevTools throttling and are refused (422) on any
  engine but Chromium. Firefox cannot emulate a mobile browser, only the screen size and touch, so the
  phone profile on Firefox is a small touch screen, not a phone user agent.
- Like Chromium here, Firefox runs in the container without its own process sandbox (it cannot create
  user namespaces under `cap_drop: ALL`); the container is the sandbox. The container fix that made
  Firefox start was the home directory's ownership (`/home/pwuser` tmpfs was `root:root`).
