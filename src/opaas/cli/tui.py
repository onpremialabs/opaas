"""The interactive OPAAS: one full screen, the meerkat keeping watch beside the menu.

`opaas` with no command, in a terminal, opens it. It is the cover page and the
menu of `banner.py` and `menu.py` laid out as one screen, in the same design
system — the sprite's palette, square corners, the pixel title — with the
meerkat of the cover page at its full size beside the menu (`mob.Sentinel`).
It comes up out of its burrow when the screen opens and then stands still,
keeping watch. It moves only when something happens: the mouse passes over it,
or an entry is chosen. Then it blinks, looks around and hops, and settles
again. `OPAAS_NO_ANIMATION=1` keeps it still.

**Still no TUI library.** Rich already draws everything here (`Live` on the
alternate screen); what it lacks is reading a key, or the mouse, with a
timeout. That is a few lines per operating system (`KeyReader`), and a
dependency for it would be a bad trade in a tool whose point is being small
enough to audit.

**The menu teaches the CLI rather than hiding it.** Entries are grouped by task,
like `opaas --help`. A read-only entry runs on the spot and the screen comes
back after it; an entry that needs arguments leaves the screen and prints the
exact command, with its placeholders, in the scrollback where it can be copied
— and every entry names the page of the documentation that explains it.

**The sprite is never cropped.** A terminal too short for it at its full size
gets it at half (`mob.shrink`); one too small for that, none, and the menu
keeps the room, scrolling when even it does not fit. There is no other mode:
any terminal gets this screen, laid out for its size. The only frames that
differ from the PNG are passing ones: the rows
still underground during the entrance, the closed eyelid of a blink, the
mirror image while it looks the other way.
"""

from __future__ import annotations

import io
import os
import re
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
from opaas.cli.mob import Sentinel
from opaas.cli.sprite import TAGLINE

__all__ = [
    "KeyReader",
    "Mouse",
    "Screen",
    "run",
    "supported",
]

#: A key that opens the selected entry's documentation in the browser.
DOCS_KEY = "docs"

#: Seconds per frame. Ten a second is smooth enough for a blink and costs nothing.
TICK = 0.1
#: How long the meerkat reacts to a chosen entry before the entry runs.
REACTION = 0.5

#: Below this the screen says how big it must be; above it, everything is
#: laid out for the room there is, the menu scrolling when it does not fit.
MIN_WIDTH, MIN_HEIGHT = 40, 6
#: The menu column's narrowest useful width, beside the sprite.
_MENU_WIDTH = 56
#: The frame's top and bottom lines.
_CHROME = 2


def _sprite_columns(factor: int) -> int:
    """The sprite column: the drawing and a margin, and room for the tagline."""
    return max(Sentinel(factor=factor).size[0] + 4, len(TAGLINE) + 2)


def _sprite_lines(factor: int) -> int:
    """The sprite's lines: the drawing, its ground and the tagline."""
    return Sentinel(factor=factor).size[1] // 2 + 2


#: Mouse reporting: every motion, in SGR coordinates (xterm, Windows Terminal,
#: iTerm2, GNOME's VTE). A terminal that knows neither ignores both.
_MOUSE_ON, _MOUSE_OFF = "\x1b[?1003h\x1b[?1006h", "\x1b[?1003l\x1b[?1006l"
_SGR_MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


@dataclass(frozen=True, slots=True)
class Mouse:
    """Where the mouse is, in terminal cells counted from 1."""

    x: int
    y: int


# --------------------------------------------------------------------- keys


class KeyReader:
    """One keystroke, or a mouse move, at a time, waiting at most `timeout`.

    A context manager because the terminal changes mode for the whole session
    and must be put back however it ends: on POSIX it leaves line mode
    (cbreak: keys arrive one by one, output is untouched); on Windows its input
    speaks VT sequences, so arrows and the mouse arrive as POSIX terminals send
    them. Both then report the mouse, which is how the meerkat knows it is
    being looked at.
    """

    def __init__(self, *, mouse: bool = True) -> None:
        self._mouse = mouse
        self._saved: object = None

    def __enter__(self) -> KeyReader:
        if os.name == "nt":
            self._saved = _windows_vt_input(True)
        else:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        if self._mouse:
            sys.stdout.write(_MOUSE_ON)
            sys.stdout.flush()
        return self

    def __exit__(self, *_: object) -> None:
        if self._mouse:
            sys.stdout.write(_MOUSE_OFF)
            sys.stdout.flush()
        if os.name == "nt":
            _windows_vt_input(False, self._saved)
        else:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def read(self, timeout: float | None) -> str | Mouse | None:
        """A normalized key, a `Mouse`, or None when the timeout passed with none."""
        char = self._char(timeout)
        if char is None:
            return None
        if char == "\x1b":
            return self._sequence()
        if char in ("\x00", "\xe0"):
            # The console's own arrows, when VT input could not be turned on.
            return {"H": UP, "P": DOWN}.get(self._char(0.05) or "", OTHER)
        return _normalize(char)

    def _sequence(self) -> str | Mouse:
        """What follows an ESC: an arrow, a mouse report, or nothing (a quit)."""
        following = ""
        while True:
            char = self._char(0.03)
            if char is None:
                break
            following += char
            if char.isalpha() or char == "~" or len(following) > 24:
                break
        return parse_sequence("\x1b" + following)

    def _char(self, timeout: float | None) -> str | None:
        if os.name == "nt":
            import msvcrt

            deadline = None if timeout is None else time.monotonic() + timeout
            while not msvcrt.kbhit():
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                time.sleep(0.005)
            return msvcrt.getwch()
        import select

        if not select.select([self._fd], [], [], timeout)[0]:
            return None
        return os.read(self._fd, 1).decode(errors="replace")


def _normalize(char: str) -> str:
    if char in ("\r", "\n"):
        return ENTER
    if char in ("\x03", "q", "Q"):
        return QUIT
    if char in ("k", "K"):
        return UP
    if char in ("j", "J"):
        return DOWN
    if char in ("o", "O"):
        return DOCS_KEY
    return OTHER


def parse_sequence(sequence: str) -> str | Mouse:
    """An escape sequence, as the key or the mouse position it reports."""
    if sequence == "\x1b":
        return QUIT
    if sequence in ("\x1b[A", "\x1bOA"):
        return UP
    if sequence in ("\x1b[B", "\x1bOB"):
        return DOWN
    match = _SGR_MOUSE.fullmatch(sequence)
    if match:
        return Mouse(int(match.group(2)), int(match.group(3)))
    return OTHER


def _windows_vt_input(enable: bool, saved: object = None) -> object:
    """Turns VT input on for the console (or back to `saved`); None if it cannot."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
        if enable:
            # ENABLE_VIRTUAL_TERMINAL_INPUT on; quick edit off, or a click
            # would start a selection instead of reaching the screen.
            kernel.SetConsoleMode(handle, (mode.value | 0x0200 | 0x0080) & ~0x0040)
            return mode.value
        if isinstance(saved, int):
            kernel.SetConsoleMode(handle, saved)
    except (AttributeError, OSError, ValueError):
        return None
    return None


# ------------------------------------------------------------------- screen


@dataclass
class Screen:
    """What the screen shows, kept apart from keys and time so it can be tested."""

    items: Sequence[MenuItem]
    status: list[tuple[str, str, str]] = field(default_factory=list)
    index: int = 0
    #: Where the sprite was last drawn, in cells from 1: (left, top, right, bottom).
    sprite_box: tuple[int, int, int, int] | None = None

    def move(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.items)

    @property
    def selected(self) -> MenuItem:
        return self.items[self.index]

    def over_sprite(self, mouse: Mouse) -> bool:
        if self.sprite_box is None:
            return False
        left, top, right, bottom = self.sprite_box
        return left <= mouse.x <= right and top <= mouse.y <= bottom

    # -- drawing

    def render(self, width: int, height: int, meerkat: Sentinel,
               now: float) -> RenderableType:
        self.sprite_box = None
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            return Panel(
                Text(f"The terminal is {width}×{height}; OPAAS needs {MIN_WIDTH}×{MIN_HEIGHT}. "
                     "Make it larger, or press q.", Style(color=PALETTE.text)),
                box=SQUARE, border_style=Style(color=PALETTE.frame),
            )
        inner_height = height - _CHROME
        # The drawing as is when it fits; half its size when only that does.
        factor = next((f for f in (1, 2)
                       if width >= _sprite_columns(f) + _MENU_WIDTH + 4
                       and inner_height >= _sprite_lines(f)), None)
        with_sprite = factor is not None
        if factor is not None:
            meerkat.factor = factor
        columns_used = _sprite_columns(factor) if factor is not None else 0
        menu_width = width - 4 - columns_used
        right = self._column(menu_width, inner_height)

        grid = Table.grid(padding=(0, 0))
        if with_sprite:
            grid.add_column(width=columns_used, no_wrap=True)
        grid.add_column(width=menu_width)
        if with_sprite:
            pad = max(0, inner_height - _sprite_lines(meerkat.factor))
            columns, rows = meerkat.size
            # Inside the frame's border and padding: the sprite starts at column 3.
            self.sprite_box = (3, 2 + pad, 2 + columns, 1 + pad + rows // 2)
            left = Group(
                *([Text()] * pad),
                *meerkat.lines(now),
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
            Text(),
        ]
        title = [*pixelfont.render(TITLE, PALETTE.accent, PALETTE.accent_dim), Text()]
        if width < pixelfont.text_width(TITLE):
            title = []
        status = [Text.assemble((f"{mark} ", Style(color=colour, bold=mark == "✓")),
                                (message, Style(color=PALETTE.muted)))
                  for mark, message, colour in self.status if mark != "·"]
        spaced, packed = self._menu(spaced=True), self._menu(spaced=False)
        # The first layout that fits, in order of what each piece is worth: the
        # pixel title goes first, then the blank lines between sections, then
        # the environment status, then the header; the menu and the selected
        # entry always stay.
        status_block = [Text(), *status]
        layouts = [
            [*title, Text(SUBTITLE, Style(color=PALETTE.text)), *header[1:], *spaced,
             Text(), *detail, *status_block],
            [*header, *spaced, Text(), *detail, *status_block],
            [*header, *packed, Text(), *detail, *status_block],
            [*header, *spaced, Text(), *detail],
            [*header, *packed, Text(), *detail],
            [*packed, Text(), *detail],
        ]
        for body in layouts:
            if _height(body, width) <= height:
                return Group(*body)
        return Group(*self._scrolled(packed, width, height))

    def _scrolled(self, menu: list[Text], width: int, height: int) -> list[Text]:
        """A short terminal: the menu scrolls around the selected entry.

        What the entry runs stays under it, cut to its command; the lines the
        window hides are marked, so the menu never looks shorter than it is.
        """
        item = self.selected
        if item.command:
            under = [Text.assemble(("$ ", Style(color=PALETTE.muted)),
                                   (item.command, Style(color=PALETTE.accent)))]
        elif item.action is not None:
            under = [Text("Enter runs it here.", Style(color=PALETTE.muted))]
        else:
            under = []
        under = [line for line in under if _height([line], width) < height - 2]
        room = max(1, height - _height(under, width))
        chosen = next(i for i, line in enumerate(menu) if line.plain.startswith(" ▸ "))
        if len(menu) <= room:
            return [*menu, *under]
        start = min(max(0, chosen - room // 2), len(menu) - room)
        window = menu[start:start + room]
        more = Style(color=PALETTE.muted, italic=True)
        if start > 0 and start != chosen:
            window[0] = Text(f"   ↑ {start + 1} more", more)
        if start + room < len(menu) and start + room - 1 != chosen:
            window[-1] = Text(f"   ↓ {len(menu) - start - room + 1} more", more)
        return [*window, *under]

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


#: Only measures how text wraps; it is never printed to.
_MEASURE = Console(file=io.StringIO(), width=200, legacy_windows=False)


def _height(lines: list[Text], width: int) -> int:
    """Terminal lines a column of text takes once wrapped at `width`."""
    return sum(max(1, len(line.wrap(_MEASURE, width))) for line in lines)


# ---------------------------------------------------------------------- run


def supported(console: Console) -> bool:
    """Whether this terminal can draw the screen at all; its size never decides.

    A small terminal still gets the screen, laid out for it. The legacy
    Windows console and `TERM=dumb` have no alternate screen to draw on.
    """
    if os.environ.get("TERM") == "dumb":
        return False
    return console.is_terminal and not console.legacy_windows


def _animate() -> bool:
    return not os.environ.get("OPAAS_NO_ANIMATION")


def _session(console: Console, screen: Screen, meerkat: Sentinel, keys: KeyReader,
             clock: Callable[[], float], open_docs: Callable[[str], object],
             reaction: float) -> MenuItem | None:
    """One stretch on the full screen; returns the chosen entry, None to leave."""
    def draw() -> RenderableType:
        return screen.render(console.width, console.height, meerkat, clock())

    painted: tuple[object, ...] | None = None

    def repaint(live: Live) -> None:
        # Only when something on screen changed: a frame of the full sprite is
        # tens of kilobytes, and a terminal fed that every tick falls behind.
        nonlocal painted
        now = clock()
        state = (meerkat.frame(now), meerkat.watching(now), screen.index,
                 console.width, console.height)
        if state != painted:
            live.update(draw(), refresh=True)
            painted = state

    with Live(draw(), console=console, screen=True, auto_refresh=False,
              redirect_stdout=False, redirect_stderr=False) as live:
        while True:
            event = keys.read(TICK)
            if isinstance(event, Mouse):
                if screen.over_sprite(event):
                    meerkat.wake(clock())
                repaint(live)
                continue
            if event is not None:
                meerkat.skip_entrance()
            if event == QUIT:
                return None
            if event == ENTER:
                # The meerkat reacts to the command before it runs.
                meerkat.wake(clock())
                until = time.monotonic() + reaction
                while time.monotonic() < until:
                    repaint(live)
                    time.sleep(TICK)
                return screen.selected
            if event in (UP, DOWN):
                screen.move(-1 if event == UP else 1)
            if event == DOCS_KEY and screen.selected.docs:
                open_docs(screen.selected.docs)
            repaint(live)


def run(console: Console, items: Sequence[MenuItem], *,
        keys: KeyReader | None = None,
        clock: Callable[[], float] = time.monotonic,
        open_docs: Callable[[str], object] = webbrowser.open,
        animate: bool | None = None,
        reaction: float = REACTION) -> None:
    """The full-screen menu, until the user leaves or picks a command to type."""
    screen = Screen(items, status=status_lines())
    meerkat = Sentinel(animate=_animate() if animate is None else animate, started=clock())
    if not meerkat.animate:
        meerkat.skip_entrance()
    with keys or KeyReader() as reader:
        try:
            _loop(console, screen, meerkat, reader, clock, open_docs, reaction)
        except KeyboardInterrupt:
            # Ctrl+C leaves, as q does: the terminal is put back on the way out.
            return


def _loop(console: Console, screen: Screen, meerkat: Sentinel, reader: KeyReader,
          clock: Callable[[], float], open_docs: Callable[[str], object],
          reaction: float) -> None:
    """Stretches on the screen, and what was chosen run in between."""
    while True:
        item = _session(console, screen, meerkat, reader, clock, open_docs, reaction)
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
        while isinstance(event := reader.read(None), Mouse):
            pass
        if event == QUIT:
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
