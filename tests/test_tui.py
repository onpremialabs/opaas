import io

import pytest
from rich.console import Console

from opaas.cli import tui
from opaas.cli.main import _menu_items
from opaas.cli.menu import DOWN, ENTER, OTHER, QUIT, UP, MenuItem
from opaas.cli.sprite import MASCOT


def _text(renderable, width=120, height=40) -> str:
    console = Console(width=width, height=height, no_color=True)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _screen(**kwargs) -> tui.Screen:
    return tui.Screen(_menu_items(), status=[("✓", "5 of 5 blocks implemented", "green")],
                      **kwargs)


class _Keys:
    """A KeyReader that replays keys, then leaves; None stands for a timeout."""

    def __init__(self, *keys):
        self.keys = list(keys)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, timeout):
        return self.keys.pop(0) if self.keys else QUIT


def _run(items, *keys, opened=None):
    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=True,
                      legacy_windows=False)
    tui.run(console, items, keys=_Keys(*keys), clock=lambda: 100.0,
            open_docs=(opened.append if opened is not None else lambda url: None),
            animate=False)
    return console.file.getvalue()


# ------------------------------------------------------------------- frames


def test_every_frame_keeps_the_sprites_size():
    """A frame of another size would make the menu beside it jump."""
    meerkat = tui.Meerkat(started=0.0)
    for moment in [0.0, 0.3, 0.9, 1.6, 4.2, 4.25, 12.65, 30.0]:
        frame = meerkat.frame(moment)
        assert len(frame) == len(MASCOT)
        assert {len(row) for row in frame} == {len(MASCOT[0])}


def test_the_meerkat_comes_up_out_of_its_burrow():
    """Head first: the top of the drawing shows at the bottom of the canvas."""
    frame = tui.rising_frame(len(MASCOT) - 4)
    assert all(set(row) == {"."} for row in frame[:-4])
    assert frame[-4:] == MASCOT[:4]


def test_the_entrance_ends_with_the_sprite_exactly_as_drawn():
    meerkat = tui.Meerkat(started=0.0)
    assert meerkat.frame(0.0) != MASCOT
    assert meerkat.frame(2.0) == MASCOT


def test_a_key_puts_the_meerkat_in_place_at_once():
    meerkat = tui.Meerkat(started=0.0)
    meerkat.skip_entrance()
    assert meerkat.frame(0.0) == MASCOT


def test_a_blink_closes_the_eye_and_touches_nothing_else():
    """The sprite is the PNG as is; the only liberty taken is the eyelid."""
    closed = tui.blink_frame()
    changed = {(y, x) for y, row in enumerate(MASCOT) for x, pixel in enumerate(row)
               if closed[y][x] != pixel}
    assert changed
    assert {y for y, _ in changed} <= set(range(5, 8))
    assert {x for _, x in changed} <= set(range(8, 12))
    assert "S" not in "".join(closed), "the eye's highlight shows through a closed lid"


def test_the_meerkat_blinks_every_few_seconds():
    meerkat = tui.Meerkat(started=0.0)
    assert meerkat.frame(tui.BLINK_EVERY + 0.05) == tui.blink_frame()
    assert meerkat.frame(tui.BLINK_EVERY + 1.0) == MASCOT


def test_without_animation_the_meerkat_stands_still():
    meerkat = tui.Meerkat(animate=False, started=0.0)
    assert {meerkat.frame(t) for t in (0.0, 0.1, tui.BLINK_EVERY + 0.05)} == {MASCOT}
    assert meerkat.watching(0.0) and meerkat.watching(1.5)


def test_animation_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("OPAAS_NO_ANIMATION", "1")
    assert tui._animate() is False


# ------------------------------------------------------------------- screen


def test_the_menu_is_grouped_by_task_like_the_help():
    text = _text(_screen().render(120, 40, tui.Meerkat(animate=False), 0.0))
    positions = [text.index(section) for section in
                 ("AUDIT", "RESULTS", "DATA AND SETUP", "SIGNED CREDENTIALS (ADVANCED)")]
    assert positions == sorted(positions)


def test_the_selected_entry_shows_its_command_and_its_documentation():
    screen = _screen()
    text = _text(screen.render(120, 40, tui.Meerkat(animate=False), 0.0))
    assert "$ opaas run --model <id>" in text
    assert "Docs: https://github.com/onpremialabs/opaas/" in text
    assert "…" not in text and "model-deployment" in text, "the link is folded, never cut"


def test_an_entry_that_runs_says_so_instead_of_showing_a_command():
    screen = _screen()
    screen.index = next(i for i, item in enumerate(screen.items) if item.label == "Recent runs")
    text = _text(screen.render(120, 40, tui.Meerkat(animate=False), 0.0))
    assert "Enter runs it here" in text


def test_the_environment_status_is_on_screen_when_there_is_room():
    text = _text(_screen().render(120, 40, tui.Meerkat(animate=False), 0.0))
    assert "✓ 5 of 5 blocks implemented" in text


def test_a_wide_terminal_shows_the_meerkat():
    text = _text(_screen().render(120, 40, tui.Meerkat(animate=False), 0.0))
    assert "small sentinel, full audit" in text


def test_a_narrow_terminal_drops_the_meerkat_but_keeps_the_menu():
    """Never cropped, never shrunk: without room it is not drawn at all."""
    text = _text(_screen().render(90, 30, tui.Meerkat(animate=False), 0.0), 90, 30)
    assert "small sentinel" not in text
    assert "Audit a model deployment" in text and "Exit" in text


def test_a_short_terminal_keeps_the_documentation_link():
    """The blank lines between sections go before anything that says something."""
    text = _text(_screen().render(90, 28, tui.Meerkat(animate=False), 0.0), 90, 28)
    assert "Docs: https://" in text
    assert "Exit" in text


@pytest.mark.parametrize(("width", "height"), [(60, 24), (90, 30), (104, 34), (160, 50)])
def test_the_screen_fills_the_terminal_and_never_overflows_it(width, height):
    lines = _text(_screen().render(width, height, tui.Meerkat(animate=False), 0.0),
                  width, height).splitlines()
    assert len(lines) == height
    assert max(len(line) for line in lines) <= width


def test_a_terminal_too_small_says_how_big_it_must_be():
    text = _text(_screen().render(50, 20, tui.Meerkat(animate=False), 0.0), 50, 20)
    assert "60×24" in text and "press q" in text


def test_the_full_screen_needs_a_real_terminal_of_some_size(monkeypatch):
    monkeypatch.delenv("OPAAS_NO_TUI", raising=False)
    def console(**kwargs):
        return Console(file=io.StringIO(), height=40, legacy_windows=False, **kwargs)

    big = console(width=120, force_terminal=True)
    assert tui.supported(big)
    assert not tui.supported(console(width=120))
    assert not tui.supported(console(width=50, force_terminal=True))
    assert not tui.supported(Console(file=io.StringIO(), width=120, height=40,
                                     force_terminal=True, legacy_windows=True))
    monkeypatch.setenv("OPAAS_NO_TUI", "1")
    assert not tui.supported(big)


# -------------------------------------------------------------------- keys


def test_arrows_move_and_wrap_around():
    screen = _screen()
    screen.move(-1)
    assert screen.selected.label == "Exit"
    screen.move(1)
    assert screen.selected.label == "Audit a model deployment"


@pytest.mark.parametrize(("char", "key"), [("\r", ENTER), ("q", QUIT), ("\x1b", QUIT),
                                           ("k", UP), ("j", DOWN), ("o", tui.DOCS_KEY),
                                           ("x", OTHER)])
def test_keys_are_normalized(char, key):
    assert tui.KeyReader._normalize(char) == key


def test_a_command_entry_leaves_the_screen_and_prints_the_command_to_copy():
    output = _run(_menu_items(), DOWN, None, ENTER)
    assert "opaas smith run --agent http --endpoint <agent-url>" in output
    assert "Fill in the <placeholders>" in output
    assert "Docs: https://github.com/onpremialabs/opaas/blob/develop/docs/guide.md#3" in output


def test_an_action_entry_runs_and_the_menu_comes_back():
    executed = []
    items = [MenuItem("a", "List", action=lambda: executed.append(1)),
             MenuItem("x", "Exit", quit=True)]
    _run(items, ENTER, OTHER, ENTER, QUIT, QUIT)
    assert executed == [1, 1]


def test_q_after_an_action_leaves_instead_of_returning():
    executed = []
    items = [MenuItem("a", "List", action=lambda: executed.append(1))]
    _run(items, ENTER, QUIT, ENTER)
    assert executed == [1]


def test_exit_leaves_without_running_anything():
    executed = []
    items = [MenuItem("x", "Exit", quit=True),
             MenuItem("a", "List", action=lambda: executed.append(1))]
    output = _run(items, ENTER)
    assert executed == []
    assert "List" not in output.split("\x1b[?1049l")[-1]


def test_o_opens_the_selected_entrys_documentation():
    opened = []
    _run(_menu_items(), "docs", QUIT, opened=opened)
    assert opened == ["https://github.com/onpremialabs/opaas/blob/develop/docs/guide.md"
                      "#1--model-deployment"]


def test_every_menu_entry_says_what_it_does_and_where_it_is_documented():
    for item in _menu_items():
        if item.quit:
            continue
        assert item.description, item.label
        assert item.docs.startswith("https://github.com/onpremialabs/opaas/"), item.label
        assert bool(item.command) != bool(item.action), item.label
