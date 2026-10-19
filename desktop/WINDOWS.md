# Running the desktop assistant on Windows

**Status: written and logic-tested, not yet run on a real Windows machine by the author.** Nothing in the
Linux test runs proves the real Windows library behaves as expected. Step 4 below is the test that does, and
`.github/workflows/desktop.yml` runs the same test on a GitHub Windows runner when you push.

## What you need

- Windows 10 or 11, Python 3.11 or newer (python.org, tick "Add to PATH"), Node.js 22, and `pnpm`
  (`npm i -g pnpm`).
- A KMR AI server the computer can reach (the web app's "Settings" creates the token).

## 1. Set up (PowerShell, in the repository folder)

```powershell
cd desktop\agent
python -m venv .venv
.venv\Scripts\pip install httpx pytest uiautomation comtypes
cd ..\app
pnpm install
```

## 2. Create a token

In the KMR AI web app: Settings, "Browser extension and desktop assistant", For: **Desktop assistant**,
create a token and copy it (shown once).

## 3. Run

```powershell
cd desktop\app
pnpm start
```

Paste the server address and the token. Press the window icon, tick a window, describe the problem, check
the preview, **Send this**. Every step the assistant proposes needs its own **Allow**.

## 4. Check that the Windows backend really works (please do this once)

```powershell
cd desktop\agent
.venv\Scripts\python -m pytest -q -rs
```

The four tests in `tests/test_platform_windows_live.py` open a real Windows Forms window and must show
`passed`, not `skipped`. They check that the agent lists the window, reads button and field names (never a
field's value), flags the password box, presses one button, types into one field, refuses a stale element and
a password field, and that the policy stops "Submit order" and the password field. If any fail, send me the
output.

For the full chain (Electron, the agent, a real window and the server) run `pnpm test:e2e` in `desktop\app`
with the KMR AI server running (`KMR_API` is the server address, default `http://localhost:8000`).

## 5. Optional: an installer

The workflow builds `kmr-agent.exe` (PyInstaller) and an NSIS installer (electron-builder) and uploads them
as a workflow artifact. Building them locally: `python build_exe.py` in `desktop\agent`, then
`pnpm dlx electron-builder --win nsis` in `desktop\app`. **These two build steps have never been run.** The
installer is unsigned, so Windows SmartScreen will warn.

## Things that differ from Linux

- It acts through UI Automation patterns (Invoke, SelectionItem, Value.SetValue). It does not move the mouse or
  send keys.
- Apps with custom drawing (games, some Electron or canvas apps) expose little to UI Automation, so little can
  be read. Browsers expose page content only when their accessibility tree is on.
- The config is `%APPDATA%\kmr-desktop\config.json`. Windows keeps `%APPDATA%` private to your account; the
  token is stored in plain text there, not in the Windows credential store.
- Windows Defender or an antivirus may flag an unsigned PyInstaller executable. That is a known false alarm
  for such files; signing the build is the fix.
- Windows can only start "when I sign in" through the app's own setting (a login item), not a service.
