"""The operating system's own folder dialog, opened for the dashboard (D86).

A page cannot hand a local path to anyone: a browser's folder input gives the
files' names, never where they are. The dashboard's server is local, though
(loopback only, D79), so it can open the system's dialog on the same desktop
and return the path the person chose.

**One process per dialog.** The dialog runs in a child process, never in the
server: macOS lets only a process's main thread open a window, and a dialog
left open must not hold the server. The child is, by platform:

- macOS: `osascript`'s *choose folder*, Finder's own panel;
- Linux: `zenity`, else `kdialog`, the desktop's own; else Tk;
- Windows: Tk, whose folder dialog there is Explorer's.

**No dialog is said, not faked.** With no desktop (a server reached through an
SSH tunnel, a Linux without a display or Tk), `DialogError` says so, and the
page falls back to its own folder list (`/api/folders`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: How long a person may keep the dialog open.
TIMEOUT_S = 900
TITLE = "Choose the folder of the environment to audit"

_TK = (
    "import sys, tkinter\n"
    "from tkinter import filedialog\n"
    "root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True); root.update()\n"
    "path = filedialog.askdirectory(initialdir=sys.argv[1] or None, mustexist=True,"
    " title=sys.argv[2], parent=root)\n"
    "root.destroy()\n"
    "sys.stdout.write(path or '')\n"
)


class DialogError(RuntimeError):
    """No folder dialog can be opened here, with why."""


def dialog_command(initial: str, *, platform: str | None = None,
                   which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """The child process that shows this platform's folder dialog, starting at `initial`."""
    platform = platform or sys.platform
    if platform == "darwin":
        start = f' default location (POSIX file "{_applescript(initial)}")' if initial else ""
        return ["osascript", "-e",
                f'POSIX path of (choose folder with prompt "{TITLE}"{start})']
    if platform.startswith("linux"):
        if which("zenity"):
            return ["zenity", "--file-selection", "--directory", f"--title={TITLE}",
                    *([f"--filename={initial.rstrip('/')}/"] if initial else [])]
        if which("kdialog"):
            return ["kdialog", "--getexistingdirectory", initial or ".", "--title", TITLE]
    return [sys.executable, "-I", "-c", _TK, initial, TITLE]


def pick_folder(initial: str = "", *, platform: str | None = None,
                which: Callable[[str], str | None] = shutil.which,
                run: Callable[..., Any] = subprocess.run) -> str | None:
    """The folder the person chose, or None if they cancelled.

    Raises `DialogError` when no dialog could be shown at all.
    """
    command = dialog_command(initial, platform=platform, which=which)
    try:
        done = run(command, capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        raise DialogError(f"{command[0]} is not installed.") from None
    except subprocess.TimeoutExpired:
        return None
    chosen = (done.stdout or "").strip()
    if done.returncode == 0:
        # Tk answers C:/x on Windows: the path is given back in this system's own form.
        return str(Path(chosen)) if chosen else None
    # zenity and kdialog answer 1, osascript -128, to a cancel: not a failure.
    said = (done.stderr or "").strip()
    if done.returncode == 1 and command[0] in ("zenity", "kdialog") and not said:
        return None
    if "-128" in said or "User canceled" in said:
        return None
    raise DialogError(said.splitlines()[-1] if said else
                      f"{command[0]} ended with code {done.returncode}.")


def _applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


__all__ = ["DialogError", "dialog_command", "pick_folder"]
