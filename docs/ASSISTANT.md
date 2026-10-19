# Visitor assistant (Phase 11)

A company installs a small widget on its **own** website. Visitors get three optional helpers.
Nothing here calls an AI model: answers are quoted from the company's own content or computed
exactly, and the API says so (`ai_used: false`, a "No AI model is used" notice).

| Module | What it does | Never does |
| --- | --- | --- |
| `guide` (11A) | Answers "what is on this website?" by quoting indexed pages, with the source URL. Says "not found" when it cannot. | Act on the site; answer from outside the indexed pages |
| `forms` (11B) | Explains and checks a form field by field, shows a review checklist, checks a document's type/size/pixels in the visitor's browser, offers a consented hand-off to support, and issues action-scoped confirmations. | Submit, pay, tick declarations, invent values, bypass CAPTCHA or validation, collect passwords/OTP/card/Aadhaar/PAN/bank details |
| `learning` (11C) | See `CHILD-LEARNING-SAFEGUARDS.md` (verifiable consent tokens, review kit, sign-off gate) | See that file |

## Install

Admin or owner: **Assistant** page, create an install, list the **exact** origins allowed
(`https://example.com`; no wildcards; `http://localhost` only for development). The install key is
shown **once**; only its SHA-256 is stored. Paste the snippet:

```html
<script src="https://YOUR-KMR-HOST/widget/kmr-widget.js"
        data-api="https://YOUR-KMR-HOST/widget-api"
        data-install-key="kmr_ik_..." defer></script>
```

Rotating or revoking a key takes effect on the next request.

## Threat model (summary)

| Threat | Control |
| --- | --- |
| Someone copies a key from page source | The key is public by nature. Requests must also come from an allowed Origin; per-install and per-visitor rate limits apply; revoke/rotate at any time. A non-browser client can forge `Origin`, so the key protects *tenant routing*, not secrecy: nothing returned is private to the company beyond what it already publishes. |
| Cross-tenant reads | Key lookup is a narrow `SECURITY DEFINER` function by hash; every later query runs under forced Row-Level Security. Tested directly in Postgres and through the API. |
| Prompt injection in pages or visitor text | No model is called. Passages that try to instruct the reader are flagged at index time and never returned. |
| Indexing as SSRF | Pages come only from the project's **ownership-verified** host, through the egress proxy, path-validated, one host only. |
| Personal data in logs/storage | Visitor text is never stored or logged. Only a previewed, consented hand-off is stored, masked, with an expiry. |
| Auto-submission | There is no submit route. A test lists every public route. Confirmations are tokens, scoped to one action and one data hash, that expire in 10 minutes. |
| Abuse/cost | Rate limits; no paid service is called. |

## Retention and deletion

- Visitor questions and form values: **not stored**.
- Hand-offs: stored only after the visitor previews the exact text and consents. Deleted
  automatically after the install's retention period (1 to 365 days, default 30) and whenever a
  new hand-off arrives; admins can delete one at any time.
- Knowledge-base pages: deleted by an admin; deleting an install's data cascades.

## Known limits

- Masking is pattern-based. It cannot recognise names or addresses; the preview says so.
- Retrieval is word overlap, not understanding: a differently worded question can miss a page.
- Document checks read metadata (type, size, pixel size). They do not judge photo quality.
- The `Origin` header can be forged by non-browser clients (see the threat table).
- No browser E2E against a third-party site yet; the widget is covered by unit tests and a local
  host page (see `frontend/public/widget/demo.html`).
