# KMR AI desktop assistant

A chat app on your computer (not in the browser). You describe a problem; if you press **Look at a
window** and choose windows, it reads their element names and label text (never what is typed into a
field), masks personal details on your computer, shows you exactly what will be sent, and sends it to
your KMR AI server. It answers, points at problems, and, only when you allow each step, can press a
button or type text in another app. Design, limits and what it never does: `docs/DESKTOP.md`.

```
desktop/agent/   Python: reads windows (AT-SPI on Linux, UI Automation on Windows), redacts, enforces the
                 policy, performs approved steps, serves a local API on 127.0.0.1 for the window
desktop/app/     Electron: the chat window, tray, start-at-login
```

## Try it (Linux)

```bash
make up && make db-init && make dev-api          # the KMR AI server (once)
make desktop-setup                                # Python venv for the agent, npm packages for the app
```

1. In the KMR AI web app: Settings, **Browser extension and desktop assistant**, For: **Desktop
   assistant**, create a token and copy it (shown once).
2. `make desktop-run`, then paste the server address (`http://localhost:8000`) and the token.
3. Press the window icon, tick a window, type the problem, check the preview, **Send this**.

Closing the window keeps the assistant running (see Settings to change that). Start it again to bring
the window back. Settings also has "Start when I sign in".

## Tests

```bash
make desktop-test     # agent (pytest, includes real windows through the accessibility bus) and app (vitest)
make desktop-e2e      # Electron + real agent + real GTK window + the running server (needs `make dev-api`)
```

Test data uses `e2e-owner-*@example.com` accounts; `make clean-test-data` removes them.

## Known limits

- **Windows is written but not yet run on Windows.** The Windows backend (`platforms/windows_uia.py`) was written
  against the `uiautomation` library's source and its logic is tested on Linux with a faithful fake. A live test that
  drives a real Windows Forms window exists, runs on a Windows machine (GitHub workflow or `WINDOWS.md` step 4) and has
  not been run yet. See `WINDOWS.md`.
- **No on-screen highlight yet.** On Wayland an app cannot draw over other windows at an exact position and
  the accessibility bus gives window-relative positions, so "Show me where" describes the element in words.
- **No keyboard shortcuts or raw mouse movement.** Other programs cannot be sent keystrokes on Wayland. Actions
  go through accessibility actions (press, set text) on one named element.
- Some apps expose little or nothing on the accessibility bus (games, some canvas apps); then it can read little.
- Web pages inside a browser are readable only if the browser exposes its accessibility tree.
- The Hindi and Hinglish wording was written without a native reviewer.
- The dev command runs Electron with `--no-sandbox` because the SUID sandbox helper is not installed. A packaged
  installer must set it up. No installer is built yet.
- The token is stored in `~/.config/kmr-desktop/config.json` (mode 600), not in an OS keyring.

## Install

- Ubuntu: `./install-ubuntu.sh`, then start "KMR Assistant" from the menu or `./run.sh`. See `UBUNTU.md`.
- Windows: see `WINDOWS.md`.
