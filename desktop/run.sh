#!/usr/bin/env bash
# Starts the KMR Assistant window (Linux). `./run.sh --check` tests the pieces and starts nothing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$HERE/agent"
APP="$HERE/app"

# A terminal opened from a snap-packaged editor carries variables that make GTK libraries abort at
# start-up ("Settings schema ... does not contain a key"). Drop them when they point into /snap.
if [[ "${GSETTINGS_SCHEMA_DIR:-}" == *"/snap/"* || "${XDG_DATA_DIRS:-}" == *"/snap/"* ]]; then
  unset GSETTINGS_SCHEMA_DIR GTK_PATH GTK_EXE_PREFIX GIO_MODULE_DIR
  export XDG_DATA_DIRS="/usr/local/share:/usr/share"
fi
unset ELECTRON_RUN_AS_NODE

if [[ "${1:-}" == "--check" ]]; then
  ok=1
  say() { printf '%-6s %s\n' "$1" "$2"; [[ "$1" == "FAIL" ]] && ok=0 || true; }
  PY="$AGENT/.venv/bin/python"
  [[ -x "$PY" ]] && say PASS "agent environment: $("$PY" --version 2>&1)" || say FAIL "agent environment missing: run ./install-ubuntu.sh"
  if [[ -x "$PY" ]]; then
    "$PY" -c "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi" 2>/dev/null \
      && say PASS "accessibility library (AT-SPI) is available" || say FAIL "AT-SPI library missing: sudo apt install python3-gi gir1.2-atspi-2.0"
    "$PY" -c "import httpx" 2>/dev/null && say PASS "httpx is installed" || say FAIL "httpx missing: run ./install-ubuntu.sh"
    count="$(cd "$AGENT" && PYTHONPATH="$AGENT" "$PY" -m kmr_desktop windows 2>/dev/null | wc -l)"
    [[ "$count" -gt 0 ]] && say PASS "the agent can see $count application(s) on the accessibility bus (names only)" \
      || say FAIL "the agent sees no applications. Is a desktop session running? (details: desktop/UBUNTU.md)"
  fi
  ELECTRON="$APP/node_modules/electron/dist/electron"
  [[ -x "$ELECTRON" ]] && say PASS "Electron $("$ELECTRON" --version 2>/dev/null)" || say FAIL "Electron missing: run ./install-ubuntu.sh"
  SANDBOX="$APP/node_modules/electron/dist/chrome-sandbox"
  if [[ -e "$SANDBOX" && "$(stat -c '%U' "$SANDBOX")" == root && -u "$SANDBOX" ]]; then
    say PASS "Electron's sandbox helper is set up"
  else
    say NOTE "Electron's sandbox helper is not set up, so the window runs with --no-sandbox (see desktop/UBUNTU.md)"
  fi
  [[ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]] && say PASS "a display is available (${XDG_SESSION_TYPE:-unknown} session)" || say FAIL "no display: run this from a desktop session"
  [[ $ok -eq 1 ]] && echo "All checks passed." || { echo "Some checks failed."; exit 1; }
  exit 0
fi

cd "$APP"
exec node scripts/start.js
