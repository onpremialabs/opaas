import io

import pytest
from rich.console import Console

from opaas.cli import tui
from opaas.cli.main import _menu_items
from opaas.cli.menu import DOWN, ENTER, OTHER, QUIT, UP, MenuItem
from opaas.cli.mob import Sentinel


def _text(renderable, width=120, height=40) -> str:
    console = Console(width=width, height=height, no_color=True)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _screen(**kwargs) -> tui.Screen:
    return tui.Screen(_menu_items(), status=[("✓", "5 of 5 blocks implemented", "green")],
                      **kwargs)


def _still() -> Sentinel:
    meerkat = Sentinel(animate=False)
    meerkat.skip_entrance()
    return meerkat


class _Keys:
    """A KeyReader that replays events, then leaves; None stands for a timeout."""

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
            animate=False, reaction=0.0)
    return console.file.getvalue()


# ------------------------------------------------------------------- screen


def test_the_menu_is_grouped_by_task_like_the_help():
    text = _text(_screen().render(120, 40, _still(), 0.0))
    positions = [text.index(section) for section in
                 ("AUDIT", "RESULTS", "DATA AND SETUP", "SIGNED CREDENTIALS (ADVANCED)")]
    assert positions == sorted(positions)


def test_the_selected_entry_shows_its_command_and_its_documentation():
    text = _text(_screen().render(120, 40, _still(), 0.0))
    assert "$ opaas run --model <id>" in text
    assert "Docs: https://github.com/onpremialabs/opaas/" in text
    assert "…" not in text and "model-deployment" in text, "the link is folded, never cut"


def test_an_entry_that_runs_says_so_instead_of_showing_a_command():
    screen = _screen()
    screen.index = next(i for i, item in enumerate(screen.items) if item.label == "Recent runs")
    text = _text(screen.render(120, 40, _still(), 0.0))
    assert "Enter runs it here" in text


def test_the_environment_status_is_on_screen_when_there_is_room():
    text = _text(_screen().render(120, 40, _still(), 0.0))
    assert "✓ 5 of 5 blocks implemented" in text


def test_a_tall_terminal_shows_the_one_meerkat_at_its_full_size():
    meerkat = _still()
    text = _text(_screen().render(120, 40, meerkat, 0.0))
    assert "small sentinel, full audit" in text
    assert meerkat.factor == 1


def test_a_shorter_terminal_shows_it_at_half_its_size_rather_than_drop_it():
    meerkat = _still()
    text = _text(_screen().render(120, 30, meerkat, 0.0), 120, 30)
    assert "small sentinel, full audit" in text
    assert meerkat.factor == 2


def test_a_narrow_terminal_drops_the_meerkat_but_keeps_the_menu():
    """Never cropped: without room it is not drawn at all."""
    text = _text(_screen().render(80, 30, _still(), 0.0), 80, 30)
    assert "small sentinel" not in text
    assert "Audit a model deployment" in text and "Exit" in text


def test_a_short_terminal_keeps_the_documentation_link():
    """The blank lines between sections go before anything that says something."""
    text = _text(_screen().render(90, 28, _still(), 0.0), 90, 28)
    assert "Docs: https://" in text
    assert "Exit" in text


@pytest.mark.parametrize(("width", "height"), [(40, 6), (60, 8), (158, 11), (60, 24),
                                               (90, 30), (104, 34), (120, 30), (160, 50)])
def test_the_screen_fills_the_terminal_and_never_overflows_it(width, height):
    lines = _text(_screen().render(width, height, _still(), 0.0), width, height).splitlines()
    assert len(lines) == height
    assert max(len(line) for line in lines) <= width


def test_a_terminal_too_small_says_how_big_it_must_be():
    text = _text(_screen().render(30, 5, _still(), 0.0), 30, 5)
    assert "40×6" in text


def test_any_real_terminal_gets_the_screen_whatever_its_size(monkeypatch):
    """One mode: size lays the screen out, it never sends you elsewhere."""
    monkeypatch.delenv("TERM", raising=False)

    def console(**kwargs):
        return Console(file=io.StringIO(), legacy_windows=False, **kwargs)

    assert tui.supported(console(width=120, height=40, force_terminal=True))
    assert tui.supported(console(width=158, height=11, force_terminal=True))
    assert not tui.supported(console(width=120, height=40)), "no terminal, no screen"
    assert not tui.supported(Console(file=io.StringIO(), width=120, height=40,
                                     force_terminal=True, legacy_windows=True))
    monkeypatch.setenv("TERM", "dumb")
    assert not tui.supported(console(width=120, height=40, force_terminal=True))


@pytest.mark.parametrize("index", range(14))
def test_a_short_terminal_scrolls_the_menu_and_keeps_the_choice_in_view(index):
    """158×11, a VS Code panel: every entry can be reached, and is seen chosen."""
    screen = _screen(index=index)
    lines = _text(screen.render(158, 11, _still(), 0.0), 158, 11).splitlines()
    assert len(lines) == 11
    chosen = [line for line in lines if "▸" in line]
    assert len(chosen) == 1 and screen.selected.label in chosen[0]


def test_the_scrolled_menu_says_how_much_it_hides():
    text = _text(_screen(index=13).render(158, 11, _still(), 0.0), 158, 11)
    assert "↑" in text and "more" in text
    text = _text(_screen(index=0).render(158, 11, _still(), 0.0), 158, 11)
    assert "↓" in text and "more" in text


def test_a_short_terminal_still_shows_the_command_to_copy():
    text = _text(_screen(index=0).render(158, 11, _still(), 0.0), 158, 11)
    assert "$ opaas run --model <id>" in text


# ------------------------------------------------------------------- mouse


def test_the_screen_knows_where_it_drew_the_meerkat():
    screen = _screen()
    lines = _text(screen.render(120, 40, _still(), 0.0)).splitlines()
    left, top, right, bottom = screen.sprite_box
    drawn = [line[left - 1:right] for line in lines[top - 1:bottom]]
    assert any(set(row) - {" "} for row in drawn[:3]), "the head is inside the box"
    assert all(set(line[left - 1:right]) <= {" "} for line in lines[1:top - 1])


def test_without_a_meerkat_on_screen_the_mouse_is_over_nothing():
    screen = _screen()
    screen.render(80, 30, _still(), 0.0)
    assert screen.sprite_box is None
    assert not screen.over_sprite(tui.Mouse(10, 10))


@pytest.mark.parametrize(("sequence", "event"), [
    ("\x1b[<35;12;20M", tui.Mouse(12, 20)),
    ("\x1b[<0;3;4m", tui.Mouse(3, 4)),
    ("\x1b[A", UP),
    ("\x1b[B", DOWN),
    ("\x1bOA", UP),
    ("\x1b", QUIT),
    ("\x1b[Z", OTHER),
])
def test_escape_sequences_are_read_as_keys_or_the_mouse(sequence, event):
    assert tui.parse_sequence(sequence) == event


@pytest.mark.parametrize(("char", "key"), [("\r", ENTER), ("q", QUIT), ("\x03", QUIT),
                                           ("k", UP), ("j", DOWN), ("o", tui.DOCS_KEY),
                                           ("x", OTHER)])
def test_keys_are_normalized(char, key):
    assert tui._normalize(char) == key


def test_the_mouse_over_the_meerkat_wakes_it():
    woken = []

    class Watched(Sentinel):
        __slots__ = ()

        def wake(self, now):
            woken.append(now)

    screen = _screen()
    meerkat = Watched()
    meerkat.skip_entrance()
    screen.render(120, 40, meerkat, 0.0)
    left, top, _, _ = screen.sprite_box
    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=True,
                      legacy_windows=False)
    keys = _Keys(tui.Mouse(1, 1), tui.Mouse(left + 5, top + 5), QUIT)
    tui._session(console, screen, meerkat, keys, lambda: 7.0, lambda url: None, 0.0)
    assert woken == [7.0], "woken once: by the move over it, not the one beside it"


# -------------------------------------------------------------------- keys


def test_arrows_move_and_wrap_around():
    screen = _screen()
    screen.move(-1)
    assert screen.selected.label == "Exit"
    screen.move(1)
    assert screen.selected.label == "Audit a model deployment"


def test_a_command_entry_leaves_the_screen_and_prints_the_command_to_copy():
    output = _run(_menu_items(), DOWN, None, tui.Mouse(1, 1), ENTER)
    assert "opaas smith run --agent http --endpoint <agent-url>" in output
    assert "Fill in the <placeholders>" in output
    assert "Docs: https://github.com/onpremialabs/opaas/blob/develop/docs/guide.md#3" in output


def test_an_action_entry_runs_and_the_menu_comes_back():
    executed = []
    items = [MenuItem("a", "List", action=lambda: executed.append(1)),
             MenuItem("x", "Exit", quit=True)]
    _run(items, ENTER, tui.Mouse(2, 2), OTHER, ENTER, QUIT, QUIT)
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
    _run(items, ENTER)
    assert executed == []


def test_ctrl_c_leaves_cleanly():
    class Interrupting(_Keys):
        def read(self, timeout):
            raise KeyboardInterrupt

    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=True,
                      legacy_windows=False)
    tui.run(console, _menu_items(), keys=Interrupting(), animate=False, reaction=0.0)


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
