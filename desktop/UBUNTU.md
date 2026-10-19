# KMR Assistant on Ubuntu

The desktop assistant is a chat window next to your apps. When you press **Look now**, it reads the
accessibility tree of one window you choose (names and roles of buttons, links and fields; never
field values, never pixels). It can point at things, and, only with your permission for each step,
press a button or fill a text field. Nothing is read in the background.

Tested on Ubuntu 26.04 (GNOME, Wayland). It uses AT-SPI, which works on both Wayland and X11.

## Install

You need Ubuntu with Python 3.11+, Node.js 20+ and a running KMR AI server.

```bash
cd desktop
./install-ubuntu.sh
```

The script:

1. Lists any missing system packages and asks before running `sudo apt install`.
2. Creates the agent's Python environment (`agent/.venv`) and installs `httpx`.
3. Installs the chat window (Electron) with pnpm and downloads its binary.
4. Adds **KMR Assistant** to your applications menu.
5. Runs a self-check and prints PASS / FAIL / NOTE lines.

Useful flags: `--yes` (do not ask), `--no-apt` (skip system packages), `--autostart` (start at
login), `--dry-run` (show what would happen), `--uninstall`, `--purge` (also remove the venv and
`node_modules`).

## First run

1. In the KMR AI web app open **Settings**, then "Browser extension and desktop assistant".
2. Choose **For: Desktop assistant**, create a token and copy it. It is shown once.
3. Start **KMR Assistant** from the applications menu, or run `./run.sh`.
4. Enter the server address (for local development `http://localhost:8000`) and paste the token.

A desktop token only works for the desktop assistant, and an extension token only for the
extension.

## Use

- Type the problem, or press a chip, then **Look at a window** and pick the window.
- Findings are highlighted with their evidence. The text sent to the server is shown to you first
  and is the exact text that is sent, after local redaction.
- A fix step ("press this button", "type this text") runs only after you click Allow for that one
  step. It expires after 2 minutes and works once. If the element changed, nothing happens.
- Never done, regardless of what you click: typing into password, one-time-code, PIN, card or ID
  fields; pressing submit / pay / delete / send / close / quit style buttons; reading password
  managers, mail, messengers, banking or sign-in windows.

## Things worth knowing

- **Browser pages.** Chrome and Firefox expose page content to AT-SPI only when accessibility is
  on. Start the browser with `--force-renderer-accessibility` (Chrome) or enable
  *Settings > Accessibility* in your desktop. For web pages the browser extension is usually the
  better tool.
- **Wayland.** Reading and acting go through accessibility, not screen capture, so no screen-share
  permission is requested. The assistant cannot draw an overlay over other apps on Wayland; it
  names the element in the chat instead.
- **Sandbox.** Electron's sandbox needs a root-owned helper. Without it the window starts with
  `--no-sandbox`, which works but gives up one layer of protection. To enable it:
  `sudo chown root:root app/node_modules/electron/dist/chrome-sandbox && sudo chmod 4755 app/node_modules/electron/dist/chrome-sandbox`
- **Snap terminals.** Running from a snap-packaged VS Code terminal breaks GTK. `run.sh` clears the
  affected variables for you.
- **Chat engine.** Unless the server is configured with an Anthropic key, answers come from the
  built-in rule-based provider, which is labeled "not AI".

## Check and troubleshoot

```bash
./run.sh --check
```

| Line | Meaning |
| --- | --- |
| FAIL Electron missing | Run `./install-ubuntu.sh` again. |
| FAIL accessibility library | `sudo apt install python3-gi gir1.2-atspi-2.0 at-spi2-core`, then reinstall. |
| sees 0 applications | Turn on Settings > Accessibility > "Always show accessibility menu", log out and in. |
| NOTE sandbox helper | See Sandbox above. Optional. |

## Uninstall

```bash
./install-ubuntu.sh --uninstall   # menu and autostart entries
./install-ubuntu.sh --purge       # also agent/.venv and app/node_modules
```

Windows: see [WINDOWS.md](WINDOWS.md). Design and safety rules: [../docs/DESKTOP.md](../docs/DESKTOP.md).
