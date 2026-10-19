#!/usr/bin/env bash
# Installs the KMR Assistant on Ubuntu (22.04 or newer; tested on 26.04 GNOME). Safe to run again.
#   ./install-ubuntu.sh               install, with a question before anything needs sudo
#   ./install-ubuntu.sh --yes         do not ask
#   ./install-ubuntu.sh --no-apt      skip system packages (you installed them yourself)
#   ./install-ubuntu.sh --autostart   also start it when you sign in
#   ./install-ubuntu.sh --uninstall   remove the menu entry and the autostart entry
#   ./install-ubuntu.sh --purge       like --uninstall, and also delete your saved settings and token
#   ./install-ubuntu.sh --dry-run     show what it would do
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0 APT=1 AUTOSTART=0 UNINSTALL=0 PURGE=0 DRY=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;; --no-apt) APT=0 ;; --autostart) AUTOSTART=1 ;;
    --uninstall) UNINSTALL=1 ;; --purge) UNINSTALL=1; PURGE=1 ;; --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
MENU="$DATA/applications/kmr-assistant.desktop"
AUTO="$CONFIG/autostart/kmr-assistant.desktop"
run() { if [[ $DRY -eq 1 ]]; then echo "would run: $*"; else "$@"; fi; }
say() { printf '\n== %s\n' "$*"; }

if [[ $UNINSTALL -eq 1 ]]; then
  say "Removing the menu entry and the autostart entry"
  run rm -f "$MENU" "$AUTO"
  if [[ $PURGE -eq 1 ]]; then
    say "Deleting your saved settings and token ($CONFIG/kmr-desktop)"
    run rm -rf "$CONFIG/kmr-desktop"
  fi
  echo "Done. The program files stay in $HERE; delete that folder to remove them completely."
  exit 0
fi

if [[ -r /etc/os-release ]]; then . /etc/os-release; fi
if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *ubuntu* && "${ID_LIKE:-}" != *debian* ]]; then
  echo "This installer is for Ubuntu or Debian. Other systems: see desktop/README.md."; exit 1
fi
[[ $EUID -eq 0 ]] && { echo "Run this as your normal user, not root. It asks for sudo only when needed."; exit 1; }

# ---- 1. system packages -------------------------------------------------------------------
# Package names changed in Ubuntu 24.04 (a "t64" suffix), so each entry lists the names to try.
NEEDED=("python3" "python3-venv" "python3-gi" "gir1.2-atspi-2.0" "at-spi2-core"
  "libgtk-3-0t64|libgtk-3-0" "libnss3" "libxss1" "libatk-bridge2.0-0t64|libatk-bridge2.0-0"
  "libgbm1" "libasound2t64|libasound2" "libnotify4" "libxtst6" "libatspi2.0-0t64|libatspi2.0-0"
  "libsecret-1-0" "xdg-utils")
if ! command -v node >/dev/null || [[ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -lt 18 ]]; then
  NEEDED+=("nodejs" "npm")
fi
if [[ $APT -eq 1 ]]; then
  say "Checking system packages"
  MISSING=()
  for entry in "${NEEDED[@]}"; do
    IFS='|' read -ra names <<<"$entry"
    have=0
    for n in "${names[@]}"; do dpkg -s "$n" 2>/dev/null | grep -q '^Status: install ok' && have=1 && break; done
    if [[ $have -eq 0 ]]; then
      for n in "${names[@]}"; do
        if apt-cache show "$n" >/dev/null 2>&1; then MISSING+=("$n"); break; fi
      done
    fi
  done
  if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Missing: ${MISSING[*]}"
    if [[ $YES -eq 0 && $DRY -eq 0 ]]; then
      read -r -p "Install them with sudo apt now? [Y/n] " answer
      [[ "${answer:-Y}" =~ ^[Nn] ]] && { echo "Skipped. Install them, then run this again with --no-apt."; exit 1; }
    fi
    run sudo apt-get install -y "${MISSING[@]}"
  else
    echo "All system packages are present."
  fi
fi

# ---- 2. the agent's Python environment (uses the system PyGObject for the accessibility bus) ----
say "Setting up the agent (Python)"
if [[ ! -x "$HERE/agent/.venv/bin/python" ]]; then
  run /usr/bin/python3 -m venv --system-site-packages "$HERE/agent/.venv"
fi
run "$HERE/agent/.venv/bin/pip" install -q --disable-pip-version-check httpx

# ---- 3. the chat window's packages ------------------------------------------------------------
say "Setting up the chat window (Electron)"
if command -v pnpm >/dev/null; then PNPM=(pnpm); else PNPM=(npx --yes pnpm@10); fi
if [[ $DRY -eq 1 ]]; then echo "would run: (cd app && ${PNPM[*]} install --config.confirmModulesPurge=false)"; else (cd "$HERE/app" && "${PNPM[@]}" install --config.confirmModulesPurge=false); fi

# pnpm may skip Electron's download step; run it ourselves if the binary is not there.
if [[ $DRY -eq 0 && ! -x "$HERE/app/node_modules/electron/dist/electron" ]]; then
  say "Downloading Electron"
  (cd "$HERE/app/node_modules/electron" && node install.js)
fi

# Electron's own sandbox needs a root-owned setuid helper. Without it the window is started with
# --no-sandbox, which works but gives up one layer of protection.
SANDBOX="$HERE/app/node_modules/electron/dist/chrome-sandbox"
if [[ -e "$SANDBOX" && ! -u "$SANDBOX" ]]; then
  say "Optional: Electron's sandbox helper"
  echo "It is not set up, so the window will run with --no-sandbox. Setting it up needs sudo:"
  echo "  sudo chown root:root '$SANDBOX' && sudo chmod 4755 '$SANDBOX'"
  if [[ $YES -eq 1 && $DRY -eq 0 ]]; then
    sudo chown root:root "$SANDBOX" && sudo chmod 4755 "$SANDBOX" || echo "(could not set it; continuing without)"
  elif [[ $DRY -eq 0 ]]; then
    read -r -p "Set it up now? [y/N] " answer
    if [[ "${answer:-N}" =~ ^[Yy] ]]; then sudo chown root:root "$SANDBOX" && sudo chmod 4755 "$SANDBOX"; fi
  fi
fi

# ---- 4. menu entry (and optional autostart) ---------------------------------------------------
say "Adding KMR Assistant to your applications menu"
entry() {
  cat <<DESKTOP
[Desktop Entry]
Type=Application
Name=KMR Assistant
Comment=AI helper that looks only at the windows you choose
Exec="$HERE/run.sh"
Icon=$HERE/app/assets/icon.png
Terminal=false
Categories=Utility;
StartupNotify=true
DESKTOP
}
if [[ $DRY -eq 1 ]]; then echo "would write: $MENU"; else mkdir -p "$(dirname "$MENU")"; entry >"$MENU"; fi
if [[ $AUTOSTART -eq 1 ]]; then
  if [[ $DRY -eq 1 ]]; then echo "would write: $AUTO"; else mkdir -p "$(dirname "$AUTO")"; { entry; echo "X-GNOME-Autostart-enabled=true"; } >"$AUTO"; fi
fi
command -v update-desktop-database >/dev/null && run update-desktop-database "$(dirname "$MENU")" 2>/dev/null || true

say "Checking the installation"
if [[ $DRY -eq 0 ]]; then "$HERE/run.sh" --check || true; fi

cat <<DONE

Installed. Start it from the applications menu ("KMR Assistant") or with:  $HERE/run.sh
First run: it asks for your KMR AI server address and a desktop token.
  Token: in the KMR AI web app open Settings, "Browser extension and desktop assistant",
  choose For: Desktop assistant, create a token and paste it.
More: desktop/UBUNTU.md
DONE
