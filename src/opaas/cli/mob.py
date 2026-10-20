"""The meerkats of the terminal: the sentinel of the menu, and the company of an audit.

Two pieces of animation, both drawn from the CLI's own grid (`sprite.MASCOT`,
generated from `docs/img/mascot.png`) and never redrawn by hand:

- **The sentinel** (`Sentinel`) is the one meerkat of `opaas`'s full screen,
  the drawing at its full size, as the cover page shows it. It comes up out of
  its burrow when the screen opens and then stands still. It moves only when
  something happens: the mouse passes over it, or a command is chosen. Then
  it blinks, looks around and hops, and settles again.
- **The company** (`Company`) keeps an audit company while it runs. The
  sentinel stands watch on the left, at half size, and other meerkats come up
  out of their burrows beside it, stay a moment and go back down, others
  coming in their place, for as long as the audit runs. It is shown by
  `audit_company()`, which `common.recorded_run` opens around every audit
  command; the run's progress bar joins it (`attach`).

Reduced sizes are the grid reduced by an integer factor (`shrink`): every
block becomes its most common colour, or nothing when it is mostly
transparent. A half keeps the drawing readable; nothing smaller is used.

Nothing here decides anything. It is a reaction to a command running, never to
what the command found (invariant 1), and `OPAAS_NO_ANIMATION=1` turns it off.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from rich.box import SQUARE
from rich.console import (
    Console,
    ConsoleOptions,
    ConsoleRenderable,
    Group,
    RenderableType,
    RenderHook,
    RenderResult,
)
from rich.control import Control
from rich.live import Live
from rich.live_render import LiveRender
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from opaas.cli.sprite import MASCOT, render
from opaas.cli.sprite import PALETTE as COLOURS

__all__ = [
    "Company",
    "Sentinel",
    "attach",
    "audit_company",
    "blink_frame",
    "mirror",
    "rising_frame",
    "shrink",
]

#: How long a meerkat takes to come up out of its burrow, as in the dashboard.
RISE = 0.65
#: How long it takes to go back down.
SINK = 0.45
#: How long the sentinel stays lively after the last thing that woke it.
LIVELY = 2.6
#: A blink, and how often one comes while a meerkat is lively.
BLINK_LENGTH, BLINK_EVERY = 0.15, 1.3

#: The eye in the full grid: rows 5–8, columns 8–11 of `MASCOT`. A blink paints
#: the upper three rows with the fur around it and leaves the bottom one dark,
#: so the closed eye reads as a lash line.
_EYE_ROWS, _EYE_COLUMNS = range(5, 8), range(8, 12)
_LID = "M"
#: A block with less ink than this is left transparent when the grid is reduced.
_INK = 0.34
#: Pixel rows above the drawing, so a hop or an overshoot has room.
_HEADROOM = 2


def _animate() -> bool:
    return not os.environ.get("OPAAS_NO_ANIMATION")


# ------------------------------------------------------------------- frames


def rising_frame(hidden: int, rows: tuple[str, ...] = MASCOT) -> tuple[str, ...]:
    """A meerkat with its lowest `hidden` rows still underground.

    The canvas keeps its size, so the head comes up first at the bottom and
    the drawing rises into place; nothing else on the screen moves.
    """
    hidden = max(0, min(hidden, len(rows)))
    blank = "." * len(rows[0])
    return (blank,) * hidden + rows[: len(rows) - hidden]


def blink_frame(rows: tuple[str, ...] = MASCOT) -> tuple[str, ...]:
    """The meerkat with its eye closed, on the full grid."""
    closed = list(rows)
    for row in _EYE_ROWS:
        line = list(closed[row])
        for column in _EYE_COLUMNS:
            line[column] = _LID
        closed[row] = "".join(line)
    return tuple(closed)


def shrink(rows: tuple[str, ...], factor: int) -> tuple[str, ...]:
    """The grid reduced by `factor`: each block becomes its most common colour.

    Ties go to the colour met first, so the result is the same on every run.
    """
    if factor == 1:
        return rows
    height, width = len(rows), len(rows[0])
    reduced = []
    for top in range(0, height, factor):
        line = []
        for left in range(0, width, factor):
            block = [rows[y][x] for y in range(top, min(top + factor, height))
                     for x in range(left, min(left + factor, width))]
            ink = [pixel for pixel in block if pixel != "."]
            line.append(Counter(ink).most_common(1)[0][0]
                        if len(ink) >= _INK * len(block) else ".")
        reduced.append("".join(line))
    return tuple(reduced)


def mirror(rows: tuple[str, ...]) -> tuple[str, ...]:
    """The meerkat facing the other way."""
    return tuple(row[::-1] for row in rows)


def _ease(t: float) -> float:
    """Out of the burrow with a little overshoot, the dashboard's easing."""
    t = min(max(t, 0.0), 1.0)
    c = 1.4
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


_FRAMES: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {}


def frames(factor: int = 1) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(eyes open, eyes shut) at one size, reduced once and kept."""
    if factor not in _FRAMES:
        _FRAMES[factor] = (shrink(MASCOT, factor), shrink(blink_frame(), factor))
    return _FRAMES[factor]


def _paint(canvas: list[list[str]], rows: tuple[str, ...], left: int, top: int) -> None:
    for y, row in enumerate(rows):
        if 0 <= top + y < len(canvas):
            for x, pixel in enumerate(row):
                if pixel != "." and 0 <= left + x < len(canvas[0]):
                    canvas[top + y][left + x] = pixel


def _ground(spans: list[tuple[int, int]], width: int) -> Text:
    """Sand the width of the canvas, with a burrow at each (start, size)."""
    cells = [SAND] * width
    for start, size in spans:
        hole = max(2, round(size * 0.6))
        for x in range(start + (size - hole) // 2, start + (size - hole) // 2 + hole):
            if 0 <= x < width:
                cells[x] = BURROW
    line = Text()
    for colour in cells:
        line.append("▀", Style(color=colour))
    return line


SAND, BURROW = COLOURS["T"], COLOURS["K"]


# ----------------------------------------------------------------- sentinel


@dataclass(slots=True)
class Sentinel:
    """The full-size meerkat of the menu: still, unless something wakes it."""

    animate: bool = True
    started: float = 0.0
    #: 1 for the drawing as is; 2, half its size, for a terminal too short for it.
    factor: int = 1
    _woken: float | None = None
    _skipped: bool = False

    def skip_entrance(self) -> None:
        """A key during the entrance puts the meerkat in place at once."""
        self._skipped = True

    def wake(self, now: float) -> None:
        """The mouse passed over it, or a command was chosen."""
        if self.animate:
            self._woken = now

    def lively(self, now: float) -> bool:
        return self._woken is not None and now - self._woken < LIVELY

    @property
    def size(self) -> tuple[int, int]:
        """(columns, pixel rows) of every frame."""
        opened, _ = frames(self.factor)
        height = len(opened) + _HEADROOM
        return len(opened[0]), height + height % 2

    def frame(self, now: float) -> tuple[str, ...]:
        width, height = self.size
        canvas = [["."] * width for _ in range(height)]
        opened, shut = frames(self.factor)
        rows, lift = opened, 0
        elapsed = now - self.started
        if self.animate and not self._skipped:
            risen = _ease(elapsed / RISE)
            lift = round(len(opened) * risen) - len(opened)
        if self.lively(now):
            since = now - (self._woken or now)
            # Blink at once, then every so often; look the other way for a
            # moment; and hop twice, two pixels, on the way.
            if since % BLINK_EVERY < BLINK_LENGTH:
                rows = shut
            if 0.5 <= since < 1.5:
                rows = mirror(rows)
            if since < 0.3 or 1.6 <= since < 1.85:
                lift += max(1, 2 // self.factor)
        _paint(canvas, rows, 0, height - len(rows) - lift)
        return tuple("".join(row) for row in canvas)

    def lines(self, now: float) -> list[Text]:
        width, _ = self.size
        return [*render(self.frame(now)), _ground([(0, width)], width)]

    def watching(self, now: float) -> bool:
        """The sentinel's pulse in the frame's title: on one second, off the next."""
        return not self.animate or int(now - self.started) % 2 == 0


# ------------------------------------------------------------------ company


#: The sentinel of the company and its visitors are drawn at half size: the
#: smallest at which the drawing keeps its eye and its fur.
COMPANY_FACTOR = 2
#: A visitor's turn: up, a stay, down, then an empty burrow before the next.
_STAY, _AWAY = 3.2, 1.6


@dataclass(slots=True)
class Company:
    """Meerkats that come and go beside the sentinel while an audit runs.

    Each burrow beside the sentinel has its own turn, offset from its
    neighbours so they never arrive together, and every other visitor faces
    the other way. The pattern is fixed, not random: the same moment always
    draws the same frame.
    """

    width: int
    animate: bool = True
    started: float = field(default_factory=time.monotonic)
    clock: Callable[[], float] = time.monotonic

    @property
    def _cell(self) -> int:
        return len(frames(COMPANY_FACTOR)[0][0])

    @property
    def burrows(self) -> int:
        """Visitors beside the sentinel that fit, two columns apart."""
        return max(0, (self.width - self._cell) // (self._cell + 2))

    @property
    def height(self) -> int:
        """Pixel rows, even, so they fall on whole terminal lines."""
        tall = len(frames(COMPANY_FACTOR)[0]) + _HEADROOM
        return tall + tall % 2

    def frame(self, now: float) -> tuple[str, ...]:
        opened, shut = frames(COMPANY_FACTOR)
        width = self._cell + self.burrows * (self._cell + 2)
        canvas = [["."] * width for _ in range(self.height)]
        elapsed = now - self.started
        # The sentinel: always up, blinking every few seconds, now and then
        # looking the other way.
        rows = shut if elapsed % 4.2 < BLINK_LENGTH and self.animate else opened
        if self.animate and elapsed % 7.0 >= 6.0:
            rows = mirror(rows)
        _paint(canvas, rows, 0, self.height - len(rows))
        for burrow in range(self.burrows):
            left = self._cell + 2 + burrow * (self._cell + 2)
            pose, sunk = self._visitor(burrow, elapsed)
            if pose is not None:
                _paint(canvas, pose, left, self.height - len(pose) + sunk)
        return tuple("".join(row) for row in canvas)

    def _visitor(self, burrow: int, elapsed: float) -> tuple[tuple[str, ...] | None, int]:
        opened, shut = frames(COMPANY_FACTOR)
        if not self.animate:
            return None, 0
        turn = RISE + _STAY + SINK + _AWAY
        # Offsets that never line up: each burrow starts a fraction of a turn later.
        since = (elapsed - 0.4 - burrow * turn * 0.37) % turn
        if elapsed < 0.4 + burrow * turn * 0.37:
            return None, 0
        height = len(opened)
        if since < RISE:
            sunk = height - round(height * _ease(since / RISE))
        elif since < RISE + _STAY:
            sunk = 0
        elif since < RISE + _STAY + SINK:
            sunk = round(height * (since - RISE - _STAY) / SINK)
        else:
            return None, 0
        visit = int((elapsed - 0.4 - burrow * turn * 0.37) // turn)
        blink = RISE < since < RISE + _STAY and (since + burrow) % 1.9 < BLINK_LENGTH
        rows = shut if blink else opened
        return (mirror(rows) if (burrow + visit) % 2 else rows), sunk

    def lines(self, now: float) -> list[Text]:
        cell = self._cell
        spans = [(0, cell)] + [(cell + 2 + b * (cell + 2), cell) for b in range(self.burrows)]
        width = cell + self.burrows * (cell + 2)
        return [*render(self.frame(now)), _ground(spans, width)]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from self.lines(self.clock())


# -------------------------------------------------------------------- stage
#
# The company is drawn on the alternate screen, as `opaas`'s menu is, and not
# under the scrolling output. Redrawn under it, the pixel art left copies of
# itself in the scrollback of Windows' terminals (ConPTY) — enough truecolour
# backgrounds on one line and the redraw stops erasing what it drew — and on
# the alternate screen there is no scrollback to leave them in. What the
# command prints meanwhile is kept, shown as the log's last lines under the
# company, and printed in full, in order, to the normal screen once the audit
# ends: the record of the run is the same as without the company.


class _Capture(RenderHook):
    """Keeps what a console prints instead of letting it reach the screen."""

    def __init__(self, stage: _Stage, console: Console) -> None:
        self.stage, self.console = stage, console

    def process_renderables(
        self, renderables: list[ConsoleRenderable]
    ) -> list[ConsoleRenderable]:
        own = [r for r in renderables if isinstance(r, (Control, LiveRender))]
        printed = [r for r in renderables if not isinstance(r, (Control, LiveRender))]
        if printed:
            self.stage.keep(self.console, printed)
        return own


@dataclass(slots=True)
class _Stage:
    live: Live
    company: Company
    title: str
    attached: list[RenderableType] = field(default_factory=list)
    #: Everything printed while the company was up: (console, renderables).
    printed: list[tuple[Console, list[ConsoleRenderable]]] = field(default_factory=list)
    #: The same, as lines of plain text, for the log's tail on screen.
    tail: list[str] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)
    changed: threading.Event = field(default_factory=threading.Event)

    def keep(self, console: Console, renderables: list[ConsoleRenderable]) -> None:
        self.printed.append((console, renderables))
        width = max(20, self.live.console.width - 4)
        for renderable in renderables:
            for line in self.live.console.render_lines(
                    renderable, self.live.console.options.update(width=width, height=None),
                    pad=False):
                self.tail.append("".join(segment.text for segment in line).rstrip())
        del self.tail[:-200]
        self.changed.set()

    def renderable(self) -> RenderableType:
        console = self.live.console
        company = self.company.lines(self.company.clock())
        used = len(company) + 2 + sum(1 for _ in self.attached) + 1
        room = max(0, console.height - used)
        log = [Text(line, Style(dim=True), overflow="ellipsis", no_wrap=True)
               for line in self.tail[-room:]] if room else []
        body = Group(*company, *self.attached, Text(), *log)
        return Panel(body, title=Text(self.title, Style(color=COLOURS["T"], bold=True)),
                     title_align="left", box=SQUARE, border_style=Style(color=COLOURS["D"]),
                     padding=(0, 1), height=console.height, width=console.width)

    def paint(self) -> None:
        """Repaints when the company moved or something was printed, and once a
        second for the progress bar's clock; most ticks change nothing."""
        last: tuple[str, ...] | None = None
        painted = 0.0
        while not self.stop.wait(TICK):
            now = time.monotonic()
            frame = self.company.frame(self.company.clock())
            if frame != last or self.changed.is_set() or now - painted >= 1.0:
                self.changed.clear()
                self.live.update(self.renderable(), refresh=True)
                last, painted = frame, now

    def replay(self) -> None:
        """What the audit printed, in full and in order, on the normal screen."""
        for console, renderables in self.printed:
            console.print(*renderables)


_STAGE: _Stage | None = None
#: Seconds between looks at whether the company moved.
TICK = 0.1
#: Lines the screen needs: the company, the frame, a progress bar and a log.
_MIN_LINES = 8


def _fits(console: Console) -> bool:
    lines = Company(console.width).height // 2 + 1
    return (console.is_terminal and not console.legacy_windows and _animate()
            and os.environ.get("TERM") != "dumb" and console.height >= lines + _MIN_LINES)


@contextmanager
def audit_company(console: Console, title: str = "opaas",
                  also: Console | None = None) -> Iterator[None]:
    """The company on screen for as long as the block runs, gone when it ends.

    `also` is the console errors go to (stderr): what it prints is kept too, so
    nothing reaches the screen under the company and nothing is lost. Nested
    audits (`audit run` runs `run` and `smith run` inside it) share the one
    company of the outermost. Without a terminal tall enough, or with
    `OPAAS_NO_ANIMATION`, it shows nothing and changes nothing.
    """
    global _STAGE
    if _STAGE is not None or not _fits(console):
        yield
        return
    company = Company(console.width - 4)
    stage = _Stage(live=Live(console=console, auto_refresh=False, screen=True,
                             redirect_stdout=True, redirect_stderr=True),
                   company=company, title=f" {title} · keeping watch ")
    stage.live.update(stage.renderable())
    captures = [(console, _Capture(stage, console))]
    if also is not None and also is not console:
        captures.append((also, _Capture(stage, also)))
    _STAGE = stage
    stage.live.start(refresh=True)
    for target, capture in captures:
        target.push_render_hook(capture)
    painter = threading.Thread(target=stage.paint, name="opaas-company", daemon=True)
    painter.start()
    try:
        yield
    finally:
        stage.stop.set()
        painter.join(timeout=1.0)
        for target, _ in reversed(captures):
            target.pop_render_hook()
        stage.live.stop()
        _STAGE = None
        stage.replay()


def attach(renderable: RenderableType) -> bool:
    """Shows `renderable` under the company, if one is on screen; False if not.

    The run's progress bar joins it this way rather than opening a second live
    display, which the terminal cannot draw beside the first.
    """
    if _STAGE is None:
        return False
    _STAGE.attached.append(renderable)
    _STAGE.changed.set()
    return True


def detach(renderable: RenderableType) -> None:
    if _STAGE is not None and renderable in _STAGE.attached:
        _STAGE.attached.remove(renderable)
        _STAGE.changed.set()
