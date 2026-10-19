# Desktop assistant (Phase 14)

A chat app you install on your computer. It is not in the browser, so it keeps running when the
browser is closed. You type a problem, it looks at the windows you let it look at, answers, shows
you where the problem is, and, only with your permission for each step, can press a button or type
for you in another app.

Owner decisions (2026-10-19): Windows and Linux first; the agent looks **only when you press "Look
now"**; it may **control the mouse/keyboard-level actions with permission for every step**. This
overrides the "desktop agents are not in MVP" line in `CLAUDE.md` for this feature, on the owner's
explicit instruction. Every other rule in `CLAUDE.md` and in `PLAN.md` (Phase 13 hard rules) still
applies and is built in, not bolted on.

## How it sees and acts (and why not screenshots)

It uses the operating system's **accessibility interface**: AT-SPI on Linux, UI Automation on
Windows. That gives names, roles, text and buttons of each window as structured data, on Wayland and
X11 alike, and it is also how an action is performed (a "press" or "set text" on a named element).

- **Seeing.** Pixels are never sent. Only the text of the elements in the windows you choose,
  after local redaction (the same patterns and the same tests as the browser extension), and the
  names of those windows. A screenshot cannot be redacted reliably (a name inside an image), and the
  hard rule is that data whose redaction cannot be shown to work is not sent.
- **Acting.** Through accessibility actions on a specific element, never by guessing pixel
  coordinates. On Wayland, other programs cannot be sent synthetic keystrokes or mouse moves, so a
  keyboard shortcut or raw pointer movement is **not** offered there.

## What it never does

| Never | How it is enforced |
| --- | --- |
| Enter a password, OTP, PIN, card number or ID number | The policy engine refuses any element that is a password field or whose name/description looks like one. This is not something a permission can unlock. |
| Press submit, pay, buy, confirm, send, delete, transfer, sign out | Refused by name and role. The agent highlights that button and **you** press it. |
| Invent what it types | Text comes from you. The model may suggest a short non-personal text, it is shown in the approval box and you can change it. A suggestion that looks like personal data is dropped. |
| Look without being asked | Nothing is read until you press **Look now** and choose windows. There is no background watching. |
| Look at sensitive apps | Password managers, banking, mail, messengers, authenticators and windows whose title says login/OTP/payment are never read, by default and not removable. You can add more. |
| Act without a fresh permission | Every step has its own approval card showing the exact element and text. An approval is for that one step, expires in 2 minutes, and cannot be created by anything but the app's own UI (the local API needs a per-run secret). |
| Send more than you saw | **What will be sent** is shown before every send and is exactly what is sent (same code path, hashed). |

## Parts

- `desktop/agent/` (Python): collects the accessibility tree, redacts and checks it, talks to the
  KMR AI server, holds the step queue, enforces the policy, and performs approved steps. It listens
  only on `127.0.0.1` and needs a secret that only the app has.
- `desktop/app/` (Electron): the chat window, window picker, "what will be sent" preview, step
  approval cards, tray, optional start at login.
- Server `/desk/v1`: token-authenticated chat endpoint. Nothing a user types or any screen text is
  stored; only metadata (counts, outcome) is audited. The server re-checks that the context holds no
  sensitive window and no leftover personal data and rejects it whole if it does.

## Look and feel

Dark card, gold accent, like the reference the owner supplied: header with name, honest status,
language switch (Auto, English, Hinglish) and theme toggle; chat bubbles; quick-reply chips; an input
bar with "Look at a window" and send; an "AI answers can be wrong" line; quick actions for website
trouble. There is no microphone button (no speech input exists yet) and no invented human persona or
photo. "Online" shows only when the app really reached the server.

## What exists and how it was tested

| Part | State |
| --- | --- |
| Server `/desk/v1` (me, chat, step reports), desktop tokens, policy, built-in checks, Anthropic adapter | Built. 52 server tests. The Anthropic adapter was tested against a fake HTTP transport only; no API key was available. |
| Agent (look, redaction, preview hash, approvals, local API, config) | Built. 65 tests, including the same labeled redaction sets as the browser extension (same results) and a contract test against the server's schema. |
| Linux backend (AT-SPI) | Built and run for real: it lists real windows, reads and drives a real GTK test window (press, type), and a test shows that looking presses and types nothing. |
| Windows backend (UI Automation) | Written, **not run on Windows**. |
| Chat window (Electron) | Built. 16 unit tests plus an end-to-end run of 36 checks: Electron, the real agent reading a real window, and the real server. |

What the end-to-end run proves: connecting, the honest status, the window picker, the preview that is
exactly what is sent (personal details masked, no field values, only the chosen window), steps being
proposed without anything being done, one allowed step pressing exactly one button once, a declined step
doing nothing, Hinglish, the window hiding but the assistant staying alive, and the window being unable to
reach the agent outside its own calls or see its secret.

## What an operator must know

- The server never stores what the person types or what was read from a screen. It stores the daily count and,
  per step, only its kind and outcome (`desk_steps`), plus audit events without content.
- `KMR_DESK_CHAT_DAILY_PER_ORG` and `KMR_DESK_REQUESTS_PER_MINUTE` limit use. With no AI key the assistant is the
  built-in helper and says "not AI" everywhere; with `KMR_AI_PROVIDER=anthropic` and a key it is Claude and says so.
- A desktop token and an extension token are different kinds and cannot be used for each other's API.

## Platform status

| | Linux (GNOME, Wayland/X11) | Windows |
| --- | --- | --- |
| Window list and text | Built, tested against real windows | Built against the same interface, **not run on Windows** |
| Press / set text | Built, tested on a test window | Same, **not run on Windows** |
| On-screen highlight | Not built (described in words) | Not built |
| Keyboard shortcuts, raw pointer | Not offered on Wayland | Not built |

Mobile (Android) is later and separate; iPhone cannot read other apps' screens at all.
