"""Builds kmr-agent.exe (Windows) or kmr-agent (Linux) with PyInstaller, so an installed app does not need
Python. Run from desktop/agent:  python build_exe.py   (installs nothing; `pip install pyinstaller` first).

Not run by the author: the Windows build is made and checked by .github/workflows/desktop.yml."""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    hidden = (
        ["--hidden-import", "kmr_desktop.platforms.windows_uia", "--hidden-import", "comtypes.client"]
        if sys.platform == "win32"
        else ["--hidden-import", "kmr_desktop.platforms.linux_atspi"]
    )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        "kmr-agent",
        "--paths",
        str(HERE),
        *hidden,
        str(HERE / "kmr_desktop" / "__main__.py"),
    ]
    return subprocess.call(command, cwd=HERE)  # noqa: S603 - fixed arguments


if __name__ == "__main__":
    sys.exit(main())
