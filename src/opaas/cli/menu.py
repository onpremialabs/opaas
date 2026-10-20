"""What an entry of `opaas`'s menu is, and the keys the menu answers to.

The menu itself is the full screen of `cli/tui.py`, the one interactive way
`opaas` has (D82). It **only appears with an interactive terminal**: piped to
a file, in CI or with output redirected, `opaas` prints its help instead, since
a certification tool is run from scripts more often than by hand.

No entry hides the CLI. Read-only ones are executed; the ones that need
arguments print the exact command to copy, which teaches you to use the tool
rather than replacing it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

UP, DOWN, ENTER, QUIT, OTHER = "up", "down", "enter", "quit", "other"


@dataclass(slots=True)
class MenuItem:
    icon: str
    label: str
    #: Command printed for copying. Empty if the action is executed.
    command: str = ""
    #: Argument-less action executed when the entry is chosen.
    action: Callable[[], None] | None = None
    quit: bool = False
    #: The task group the menu lists it under.
    section: str = ""
    #: One sentence on what the entry does, shown beside the menu.
    description: str = ""
    #: Where it is documented (`cli/docs.py`).
    docs: str = ""


def is_interactive() -> bool:
    """Without a terminal on both ends, a menu cannot work."""
    return sys.stdin.isatty() and sys.stdout.isatty()
