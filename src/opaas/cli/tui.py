"""The interactive OPAAS: one full screen, the meerkat keeping watch beside the menu.

`opaas` with no command, in a terminal, opens it. It is the cover page and the
menu of `banner.py` and `menu.py` laid out as one screen, in the same design
system — the sprite's palette, square corners, the pixel title — and with the
meerkat alive: it comes up out of its burrow when the screen opens, then
stands watch and blinks. That is the whole animation. Nothing moves that
would compete with the text, and `OPAAS_NO_ANIMATION=1` stills it.

**Still no TUI library.** Rich already draws everything here (`Live` on the
alternate screen); what it lacks is reading a key with a timeout, which the
animation needs and which is twenty lines per operating system (`KeyReader`).
A dependency for that would be a bad trade in a tool whose point is being
small enough to audit.

**The menu teaches the CLI rather than hiding it.** Entries are grouped by task,
like `opaas --help`. A read-only entry runs on the spot and the screen comes
back after it; an entry that needs arguments leaves the screen and prints the
exact command, with its placeholders, in the scrollback where it can be copied
— and every entry names the page of the documentation that explains it.

**The sprite is still never cropped or shrunk.** When the terminal is too
small for it, the screen drops it and keeps the menu (`banner.py` makes the
same choice). The only frames that differ from the PNG are transient: the
rows still underground during the entrance, and the closed eyelid of a blink;
`tests/test_tui.py` checks that a blink touches the eye and nothing else.
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from rich.box import SQUARE
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from opaas import OPAAS_SPEC_VERSION, __version__
from opaas.cli import pixelfont
from opaas.cli.banner import PALETTE, SUBTITLE, TITLE, status_lines
from opaas.cli.menu import DOWN, ENTER, OTHER, QUIT, UP, MenuItem
from opaas.cli.sprite import MASCOT, TAGLINE, WIDTH, render

__all__ = [
    "KeyReader",
    "Meerkat",
    "Screen",
    "blink_frame",
    "rising_frame",
    "run",
    "supported",
]

#: A key that opens the selected entry's documentation in the browser.
DOCS_KEY = "docs"

#: Seconds per frame. Ten a second is smooth enough for a blink and costs nothing.
TICK = 0.1
#: Pixel rows the meerkat rises per frame: out of the burrow in about 1.5 s.
RISE_PER_TICK = 4
#: A blink every few seconds, and every third one doubled, the way an animal does.
BLINK_EVERY, BLINK_LENGTH = 4.2, 0.15

#: The eye in the grid: rows 5–8, columns 8–11 of `MASCOT`. A blink paints the
#: upper three rows with the fur around it and leaves the bottom one dark, so
#: the closed eye reads as a lash line.
_EYE_ROWS, _EYE_COLUMNS = range(5, 8), range(8, 12)
_LID = "M"

#: Below this the full screen is not worth drawing; `opaas` falls back to the
#: cover page and the inline menu.
MIN_WIDTH, MIN_HEIGHT = 60, 24
#: The menu column's narrowest useful width, beside the sprite.
_MENU_WIDTH = 56
_SPRITE_LINES = len(MASCOT) // 2
#: The frame's top and bottom lines.
_CHROME = 2


# ------------------------------------------------------------------- frames


def rising_frame(hidden: int, rows: tuple[str, ...] = MASCOT) -> tuple[str, ...]:
    """The meerkat with its lowest `hidden` rows still underground.

    The canvas keeps its size, so the head comes up first at the bottom and
    the drawing rises into place; nothing else on the screen moves.
    """
    hidden = max(0, min(hidden, len(rows)))
    blank = "." * len(rows[0])
    return (blank,) * hidden + rows[: len(rows) - hidden]


def blink_frame(rows: tuple[str, ...] = MASCOT) -> tuple[str, ...]:
    """The meerkat with its eye closed."""
    closed = list(rows)
    for row in _EYE_ROWS:
        line = list(closed[row])
        for column in _EYE_COLUMNS:
            line[column] = _LID
        closed[row] = "".join(line)
    return tuple(closed)


_CLOSED = blink_frame()


@dataclass(slots=True)
class Meerkat:
    """Which frame to show at a given moment. Time comes in; nothing sleeps."""

    animate: bool = True
    started: float = 0.0
    _skipped: bool = False

    def skip_entrance(self) -> None:
        """Any key during the entrance puts the meerkat in place at once."""
        self._skipped = True

    def frame(self, now: float) -> tuple[str, ...]:
        if not self.animate:
            return MASCOT
        elapsed = now - self.started
        hidden = 0 if self._skipped else len(MASCOT) - int(elapsed / TICK) * RISE_PER_TICK
        if hidden > 0:
            return rising_frame(hidden)
        cycle, phase = divmod(elapsed, BLINK_EVERY)
        blinking = phase < BLINK_LENGTH or (
            int(cycle) % 3 == 2 and 2 * BLINK_LENGTH <= phase < 3 * BLINK_LENGTH
        )
        return _CLOSED if blinking and elapsed > BLINK_EVERY / 2 else MASCOT

    def watching(self, now: float) -> bool:
        """The sentinel's pulse in the frame's title: on one second, off the next."""
        return not self.animate or int(now - self.started) % 2 == 0


# --------------------------------------------------------------------- keys


class KeyReader:
    """One keystroke at a time, waiting at most `timeout` seconds for it.

    A context manager because on POSIX the terminal leaves line mode for the
    whole session (cbreak: keys arrive one by one, output is untouched) and
    must be put back however the session ends.
    """

    def __enter__(self) -> KeyReader:
        if os.name != "nt":
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *_: object) -> None:
        if os.name != "nt":
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def read(self, timeout: float | None) -> str | None:
        """A normalized key, or None when the timeout passed with none."""
        return self._read_windows(timeout) if os.name == "nt" else self._read_posix(timeout)

    @staticmethod
    def _normalize(char: str) -> str:
        if char in ("\r", "\n"):
            return ENTER
        if char in ("\x03", "\x1b", "q", "Q"):
            return QUIT
        if char in ("k", "K"):
            return UP
        if char in ("j", "J"):
            return DOWN
        if char in ("o", "O"):
            return DOCS_KEY
        return OTHER

    def _read_windows(self, timeout: float | None) -> str | None:
        import msvcrt

        deadline = None if timeout is None else time.monotonic() + timeout
        while not msvcrt.kbhit():
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(0.01)
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return {"H": UP, "P": DOWN}.get(msvcrt.getwch(), OTHER)
        return self._normalize(char)

    def _read_posix(self, timeout: float | None) -> str | None:
        import select

        if not select.select([self._fd], [], [], timeout)[0]:
            return None
        char = os.read(self._fd, 1).decode(errors="replace")
        if char == "\x1b":
            # An arrow is ESC [ A; a bare ESC is a quit. The rest of a sequence
            # arrives within milliseconds or not at all.
            if not select.select([self._fd], [], [], 0.03)[0]:
                return QUIT
            following = os.read(self._fd, 2).decode(errors="replace")
            return {"[A": UP, "[B": DOWN, "OA": UP, "OB": DOWN}.get(following, OTHER)
        return self._normalize(char)


# ------------------------------------------------------------------- screen


@dataclass
class Screen:
    """What the screen shows, kept apart from keys and time so it can be tested."""

    items: Sequence[MenuItem]
    status: list[tuple[str, str, str]] = field(default_factory=list)
    index: int = 0

    def move(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.items)

    @property
    def selected(self) -> MenuItem:
        return self.items[self.index]

    # -- drawing

    def render(self, width: int, height: int, meerkat: Meerkat, now: float) -> RenderableType:
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            return Panel(
                Text(f"The terminal is {width}×{height}; OPAAS needs {MIN_WIDTH}×{MIN_HEIGHT}. "
                     "Make it larger, or press q.", Style(color=PALETTE.text)),
                box=SQUARE, border_style=Style(color=PALETTE.frame),
            )
        inner_height = height - _CHROME
        with_sprite = (width >= WIDTH + _MENU_WIDTH + 8
                       and inner_height >= _SPRITE_LINES + 2)
        menu_width = width - 6 - (WIDTH + 4 if with_sprite else 0)
        right = self._column(menu_width, inner_height)

        grid = Table.grid(padding=(0, 0))
        if with_sprite:
            grid.add_column(width=WIDTH + 4, no_wrap=True)
        grid.add_column(width=menu_width)
        if with_sprite:
            left = Group(
                *([Text()] * max(0, inner_height - _SPRITE_LINES - 2)),
                *render(meerkat.frame(now)),
                # The burrow's mound, right under the feet.
                Text("▀" * WIDTH, Style(color=PALETTE.frame)),
                Text(TAGLINE, Style(color=PALETTE.muted, italic=True)),
            )
            grid.add_row(left, right)
        else:
            grid.add_row(right)

        pulse = Style(color=PALETTE.ok if meerkat.watching(now) else PALETTE.muted)
        title = Text.assemble(("● ", pulse), ("keeping watch", Style(color=PALETTE.muted)))
        return Panel(
            grid,
            title=title,
            title_align="right",
            subtitle=Text("↑↓ move · Enter choose · o docs · q quit",
                          Style(color=PALETTE.muted, italic=True)),
            subtitle_align="left",
            box=SQUARE,
            border_style=Style(color=PALETTE.frame),
            padding=(0, 1),
            width=width,
            height=height,
        )

    def _column(self, width: int, height: int) -> RenderableType:
        detail = self._detail(width)
        header = [
            Text.assemble((TITLE.lower(), Style(color=PALETTE.accent, bold=True)),
                          (f"  {SUBTITLE}", Style(color=PALETTE.text))),
            Text(f"v{__version__} · methodology {OPAAS_SPEC_VERSION}",
                 Style(color=PALETTE.muted)),
        ]
        status = [Text.assemble((f"{mark} ", Style(color=colour, bold=mark == "✓")),
                                (message, Style(color=PALETTE.muted)))
                  for mark, message, colour in self.status if mark != "·"]
        # What there is room for, in order of how much it is worth: the blank
        # lines between sections go first, then the environment status; the
        # pixel title only comes when everything else fits.
        menu = self._menu(spaced=True)
        needed = len(header) + 1 + len(menu) + 1 + len(detail)
        if needed > height:
            menu = self._menu(spaced=False)
            needed = len(header) + 1 + len(menu) + 1 + len(detail)
        if needed + 1 + len(status) <= height:
            body = [*header, Text(), *menu, Text(), *detail, Text(), *status]
            needed += 1 + len(status)
        else:
            body = [*header, Text(), *menu, Text(), *detail]
        title = pixelfont.render(TITLE, PALETTE.accent, PALETTE.accent_dim)
        if needed + len(title) + 1 <= height and width >= pixelfont.text_width(TITLE):
            body = [*title, Text(), *body[1:]]
        return Group(*body)

    def _menu(self, *, spaced: bool = True) -> list[Text]:
        lines: list[Text] = []
        section = None
        for position, item in enumerate(self.items):
            if item.section and item.section != section:
                if section is not None and spaced:
                    lines.append(Text())
                section = item.section
                lines.append(Text(section.upper(), Style(color=PALETTE.accent_dim, bold=True)))
            chosen = position == self.index
            line = Text()
            line.append(" ▸ " if chosen else "   ", Style(color=PALETTE.accent))
            line.append(f"{item.icon}  ",
                        Style(color=PALETTE.accent if chosen else PALETTE.muted))
            line.append(item.label, Style(color=PALETTE.text, bold=True) if chosen
                        else Style(color=PALETTE.muted))
            lines.append(line)
        return lines

    def _detail(self, width: int) -> list[Text]:
        item = self.selected
        rule = Text("─" * max(10, width - 2), Style(color=PALETTE.frame))
        lines = [rule]
        if item.description:
            lines.append(Text(item.description, Style(color=PALETTE.text)))
        if item.quit:
            lines.append(Text("Leaves OPAAS.", Style(color=PALETTE.muted)))
        elif item.action is not None:
            lines.append(Text("Enter runs it here, and the menu comes back.",
                              Style(color=PALETTE.muted)))
        elif item.command:
            lines.append(Text.assemble(("$ ", Style(color=PALETTE.muted)),
                                       (item.command, Style(color=PALETTE.accent))))
        if item.docs:
            # Folded, never cut with an ellipsis: half a URL leads nowhere. Folded
            # here rather than by Rich so the lines it takes are counted.
            text, size = f"Docs: {item.docs}", max(10, width - 2)
            lines.extend(Text(text[start:start + size], Style(color=PALETTE.muted))
                         for start in range(0, len(text), size))
        return lines


# ---------------------------------------------------------------------- run


def supported(console: Console) -> bool:
    """Whether this terminal gets the full screen, or the inline cover and menu."""
    if os.environ.get("OPAAS_NO_TUI") or os.environ.get("TERM") == "dumb":
        return False
    # The legacy Windows console has no alternate screen to draw on.
    if console.legacy_windows or not console.is_terminal:
        return False
    return console.width >= MIN_WIDTH and console.height >= MIN_HEIGHT


def _animate() -> bool:
    return not os.environ.get("OPAAS_NO_ANIMATION")


def _session(console: Console, screen: Screen, meerkat: Meerkat, keys: KeyReader,
             clock: Callable[[], float], open_docs: Callable[[str], object]) -> MenuItem | None:
    """One stretch on the full screen; returns the chosen entry, None to leave."""
    def draw() -> RenderableType:
        return screen.render(console.width, console.height, meerkat, clock())

    with Live(draw(), console=console, screen=True, auto_refresh=False,
              redirect_stdout=False, redirect_stderr=False) as live:
        while True:
            key = keys.read(TICK)
            if key is not None:
                meerkat.skip_entrance()
            if key == QUIT:
                return None
            if key == ENTER:
                return screen.selected
            if key in (UP, DOWN):
                screen.move(-1 if key == UP else 1)
            if key == DOCS_KEY and screen.selected.docs:
                open_docs(screen.selected.docs)
            live.update(draw(), refresh=True)


def run(console: Console, items: Sequence[MenuItem], *,
        keys: KeyReader | None = None,
        clock: Callable[[], float] = time.monotonic,
        open_docs: Callable[[str], object] = webbrowser.open,
        animate: bool | None = None) -> None:
    """The full-screen menu, until the user leaves or picks a command to type."""
    screen = Screen(items, status=status_lines())
    meerkat = Meerkat(animate=_animate() if animate is None else animate, started=clock())
    with keys or KeyReader() as reader:
        while True:
            item = _session(console, screen, meerkat, reader, clock, open_docs)
            if item is None or item.quit:
                return
            if item.action is None:
                _print_command(console, item)
                return
            console.print()
            item.action()
            console.print()
            console.print(Text("Any key returns to the menu · q leaves",
                               Style(color=PALETTE.muted, italic=True)))
            if reader.read(None) == QUIT:
                return


def _print_command(console: Console, item: MenuItem) -> None:
    """The command left in the scrollback, where it can be copied and filled in."""
    console.print()
    console.print(Text(f"  {item.label}", Style(color=PALETTE.text, bold=True)))
    if item.description:
        console.print(Text(f"  {item.description}", Style(color=PALETTE.muted)))
    console.print()
    console.print(Text(f"  {item.command}", Style(color=PALETTE.accent)))
    if "<" in item.command:
        console.print(Text("  Fill in the <placeholders> and run it.",
                           Style(color=PALETTE.muted, italic=True)))
    if item.docs:
        console.print(Text(f"  Docs: {item.docs}", Style(color=PALETTE.muted)))
    console.print()
