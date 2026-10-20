"""The meerkat, in pixels.

The meerkat is the group's sentinel: it stands up on its hind legs and keeps
watch while the others eat. That is exactly what this tool does with a
deployment, so it serves as the mascot.

It is drawn with half blocks (▀ and ▄), which give two vertical pixels per
terminal cell and therefore nearly square pixels. Transparent ones paint no
background, so the drawing looks the same on light and dark terminals.

The grid does not live here: it comes from the original PNG via
`scripts/sprite_from_png.py`. This module only knows how to paint it.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

from ._sprite_data import MASCOT, PALETTE

__all__ = ["HEIGHT_IN_LINES", "PALETTE", "MASCOT", "TAGLINE", "WIDTH", "render", "render_svg"]

TAGLINE = "small sentinel, full audit"

WIDTH = len(MASCOT[0])
HEIGHT_IN_LINES = len(MASCOT) // 2


def render(rows: tuple[str, ...] = MASCOT) -> list[Text]:
    """Turns the grid into terminal lines, two rows per line.

    Neighbouring cells of the same glyph and colours are written as one run:
    the same picture in a fraction of the bytes, which an animation redrawing
    it ten times a second needs (a terminal that falls behind smears frames).
    """
    lines: list[Text] = []
    for index in range(0, len(rows), 2):
        top = rows[index]
        bottom = rows[index + 1] if index + 1 < len(rows) else "." * len(top)
        cells: list[tuple[str, Style | None]] = []
        for column in range(len(top)):
            upper = PALETTE.get(top[column])
            lower = PALETTE.get(bottom[column])
            if upper and lower:
                cells.append(("▀", Style(color=upper, bgcolor=lower)))
            elif upper:
                cells.append(("▀", Style(color=upper)))
            elif lower:
                cells.append(("▄", Style(color=lower)))
            else:
                cells.append((" ", None))
        line = Text()
        start = 0
        for end in range(1, len(cells) + 1):
            if end == len(cells) or cells[end] != cells[start]:
                glyph, style = cells[start]
                line.append(glyph * (end - start), style)
                start = end
        lines.append(line)
    return lines


def render_svg(scale: int = 8) -> str:
    """The same sprite as SVG, for the README.

    It comes from the same data as the terminal drawing, so they cannot
    diverge: a test compares the committed file against this output.
    """
    width, height = WIDTH * scale, len(MASCOT) * scale
    rects = [
        f'<rect x="{x * scale}" y="{y * scale}" width="{scale}" height="{scale}" '
        f'fill="{PALETTE[pixel]}"/>'
        for y, row in enumerate(MASCOT)
        for x, pixel in enumerate(row)
        if pixel in PALETTE
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" shape-rendering="crispEdges" '
        f'role="img" aria-label="Meerkat in pixels, the OPAAS mascot">\n'
        + "\n".join(rects)
        + "\n</svg>\n"
    )
