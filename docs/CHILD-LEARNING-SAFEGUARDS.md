# Child learning support: safeguards and open items

Status: **beta, not production-ready.** The code enforces the technical safeguards below. Items
marked **OPEN** need people (a lawyer, teachers, the school or company that installs the widget)
and have not been done. The API reports `production_ready: false` until they are.

## What the learning module does

- Accepts a **typed** question (300 characters at most) in English, Hindi or Hinglish.
- Solves arithmetic, percentages, one-variable linear equations and "is my answer right?" checks
  **exactly**, shows the steps, and re-computes the result a second way. It says when it could not
  double-check.
- For anything else it **quotes the school's own learning material** (pages the installer added)
  with the source, or says "not found" and suggests asking a teacher. It does not use the
  internet, an AI model, or the website-guide pages.
- Asks a clarifying question when a question is ambiguous (for example `6/2(1+2)`) or unreadable.
- Every answer carries a reminder that it can be wrong. It never claims to be guaranteed correct.

## Not supported (on purpose)

- **Image questions.** No upload or image understanding exists. The config says `image_input: false`.
- Open-ended factual claims outside the installer's material.
- Accounts, profiles, history, or any tracking of a learner.

## Age bands

`6-8`, `9-11`, `12-14`, `15-17`, `18+`. Only a band is sent, never a birth date or exact age.
Bands under 18 need a `guardian_confirmed` flag (see OPEN 1). The youngest band is limited to
whole numbers up to 10,000 and non-negative results; `6-8` and `9-11` get no algebra. Beyond a
band's level the answer is "ask your teacher", not a guess.

## Data handling (enforced by code and tests)

- A question is **not stored** anywhere. Tests search every table for a unique marker after asking.
- Raw text is **not echoed back**: only the normalized arithmetic is shown. Phone-like and ID-like
  numbers, emails and tokens are masked before the question is read.
- Request bodies are never logged (the access log holds method, path and status only).
- Each install has its own tenant boundary (Row-Level Security); retrieval never crosses tenants.
- No learner identity is collected, so there is nothing to delete per learner. Hand-off summaries
  (11B) are a separate feature with consent, masking and an expiry date.

## Verifiable guardian consent (built)

A browser checkbox is only a statement, so by default an install now requires a **consent token** for
every learner under 18 (`learning_consent_required`, on by default).

- The installing organization collects the guardian's consent in its **own** process (a signed form,
  an app, a school record) and then records it in KMR AI: its own opaque reference (`label`, for
  example a class code or enrolment number, **not a name**), the age group, how it collected the
  consent (`consent_basis`), and a validity period (at most 366 days).
- KMR AI returns a signed token **once**. The installer's own page hands it to the learner's session
  with `KMRWidget.setConsent(token)` or `data-consent-token`. It is kept in memory only and sent as
  the `X-KMR-Consent` header.
- A request is accepted only if the token is correctly signed, belongs to **this** install, has not
  expired or been revoked, and covers the age group being used (a learner cannot claim an older
  group than was consented). Revoking takes effect on the next request.
- No learner name, birth date or contact detail is stored. Issuing and revoking are audited without
  the token or the reference.
- **What this does not do:** KMR AI cannot check that the consent really was collected. The installer
  stays responsible for that. Whether this arrangement satisfies India's DPDP Act is the lawyer's
  question (OPEN 1), not something the code can settle.
- An admin can switch enforcement off (back to the checkbox). The config then reports
  `consent_required: false` and the module cannot be production-ready in spirit; the gate below does
  not look at this per-install switch, so **do not use the checkbox mode with real learners**.

## The production-ready gate (built)

`production_ready` in the widget config is no longer a constant. It is true only when all of this holds
(`backend/app/assistant/readiness.py`):

1. the automated evaluation passes every case;
2. a **legal**, a **teaching** and a **language** (Hindi/Hinglish) sign-off are recorded in
   `benchmarks/learning/signoffs.json`, each approved, naming the reviewer and organization, not older
   than 12 months;
3. each sign-off is bound to the exact `cases.json` and solver/wording source (`learning.py`) it
   reviewed. Changing either invalidates it, like a diff-bound approval;
4. each sign-off is signed with `KMR_SIGNOFF_SECRET`, so the file cannot be edited by hand.

The repository ships with **no sign-offs**, and a test fails if it does. The signature shows a record
was not tampered with; it cannot show that the named people really reviewed anything.

### How the reviews are done

```bash
cd backend
.venv/bin/python -m app.assistant.review_kit export     # writes benchmarks/learning/review/*.csv
```

1. **Teachers** (at least two, qualified for the age groups): fill `teaching-review-sheet.csv`
   (is the answer correct, are the steps clear for the age, is the level suitable) for **every** case.
   Record it: `review_kit import FILE.csv --reviewer "Name" --qualification "B.Ed, 10 years" --kind teaching`.
2. **Native Hindi and Hinglish speakers** (at least two): fill `language-review-sheet.csv`
   (`wording_natural`) and record it with `--kind language`.
3. `review_kit summarize` shows coverage, approval rate and flagged cases. A flagged case must be fixed
   (which changes the digests and requires a new review) before signing.
4. `review_kit sign --role teaching|language ...` refuses unless: every case has at least 2 reviewers,
   approval is at least 95% (teaching) or 90% (language), and nothing is flagged.
5. **Legal:** a lawyer reviews this document, the consent design and the data handling, and gives a
   written opinion. `review_kit sign --role legal --document opinion.pdf ...` records its hash.

## OPEN items (need people; the code cannot do these)

1. **Legal review** (DPDP Act: verifiable parental consent, data of children, retention, the
   installer's duties). Not done.
2. **Teacher review** of explanations and level limits. `cases.json` (81 cases) is written by the
   author and not reviewed by any teacher. Not done.
3. **Hindi/Hinglish wording review** by native speakers. The Hindi and Hinglish wording, including the
   new consent message, was written by an AI assistant and has not been checked. Not done.
4. **Content quality.** Quoted answers are only as good as the material the installer provides.
5. **Retention and incident process** for the installing organization.

Until 1 to 3 are recorded and signed, the API reports `production_ready: false` and lists the
blockers. Do not describe this module as production-ready, safe for children, or accurate.
