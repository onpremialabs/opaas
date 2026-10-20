"""The meerkats of the terminal: the menu's sentinel, and an audit's company."""

import io

from rich.console import Console

from opaas.cli import mob
from opaas.cli.mob import LIVELY, Company, Sentinel
from opaas.cli.sprite import MASCOT


def _terminal(height=40, width=120) -> Console:
    return Console(file=io.StringIO(), width=width, height=height, force_terminal=True,
                   legacy_windows=False)


# ------------------------------------------------------------------- frames


def test_a_blink_closes_the_eye_and_touches_nothing_else():
    """The sprite is the PNG as is; the only liberty taken is the eyelid."""
    closed = mob.blink_frame()
    changed = {(y, x) for y, row in enumerate(MASCOT) for x, pixel in enumerate(row)
               if closed[y][x] != pixel}
    assert changed
    assert {y for y, _ in changed} <= set(range(5, 8))
    assert {x for _, x in changed} <= set(range(8, 12))
    assert "S" not in "".join(closed), "the eye's highlight shows through a closed lid"


def test_the_meerkat_comes_up_out_of_its_burrow():
    """Head first: the top of the drawing shows at the bottom of the canvas."""
    frame = mob.rising_frame(len(MASCOT) - 4)
    assert all(set(row) == {"."} for row in frame[:-4])
    assert frame[-4:] == MASCOT[:4]


def test_full_size_is_the_drawing_as_is():
    assert mob.shrink(MASCOT, 1) == MASCOT


def test_half_size_is_the_grid_reduced_never_redrawn():
    """Every pixel of the half-size meerkat is the commonest colour of its block."""
    reduced = mob.shrink(MASCOT, 2)
    assert len(reduced) == len(MASCOT) // 2 and len(reduced[0]) == len(MASCOT[0]) // 2
    for y, row in enumerate(reduced):
        for x, pixel in enumerate(row):
            block = {MASCOT[2 * y + dy][2 * x + dx] for dy in (0, 1) for dx in (0, 1)}
            assert pixel in block


def test_half_size_keeps_the_eye_and_its_blink():
    opened, shut = mob.frames(2)
    assert "K" in "".join(opened[2:5]), "the eye survives the reduction"
    assert opened != shut


def test_looking_the_other_way_is_the_mirror_image():
    assert mob.mirror(mob.mirror(MASCOT)) == MASCOT
    assert mob.mirror(MASCOT)[0] == MASCOT[0][::-1]


# ----------------------------------------------------------------- sentinel


def _settled(**kwargs) -> Sentinel:
    meerkat = Sentinel(started=0.0, **kwargs)
    meerkat.skip_entrance()
    return meerkat


def test_every_frame_of_the_sentinel_keeps_its_size():
    """A frame of another size would make the menu beside it jump."""
    meerkat = Sentinel(started=0.0)
    meerkat.wake(3.0)
    width, height = meerkat.size
    for moment in [0.0, 0.2, 0.5, 1.0, 3.0, 3.1, 3.6, 4.7, 5.0, 9.0]:
        frame = meerkat.frame(moment)
        assert len(frame) == height and {len(row) for row in frame} == {width}
    assert height % 2 == 0, "pixel rows fall on whole terminal lines"


def test_it_is_the_full_drawing_unless_asked_for_half():
    assert Sentinel().size[0] == len(MASCOT[0])
    assert Sentinel(factor=2).size[0] == len(MASCOT[0]) // 2


def test_it_comes_up_out_of_its_burrow_when_the_screen_opens():
    meerkat = Sentinel(started=0.0)
    standing = _settled().frame(0.0)
    assert meerkat.frame(0.0) != standing
    assert meerkat.frame(2.0) == standing


def test_once_up_it_stands_still():
    """It moves only when something happens: no blink, no turn, on its own."""
    meerkat = _settled()
    assert len({meerkat.frame(t) for t in [x * 0.05 for x in range(400)]}) == 1


def test_the_mouse_or_a_command_wakes_it_and_it_settles_again():
    meerkat = _settled()
    still = meerkat.frame(0.0)
    meerkat.wake(10.0)
    lively = {meerkat.frame(10.0 + x * 0.05) for x in range(int(LIVELY / 0.05))}
    assert len(lively) >= 3, "it blinks, looks around and hops"
    assert mob.mirror(still) in lively or any(f != still for f in lively)
    assert meerkat.frame(10.0 + LIVELY + 0.1) == still


def test_without_animation_nothing_wakes_it():
    meerkat = _settled(animate=False)
    meerkat.wake(1.0)
    assert {meerkat.frame(1.0 + x * 0.1) for x in range(30)} == {meerkat.frame(0.0)}
    assert meerkat.watching(0.0) and meerkat.watching(1.5)


# ------------------------------------------------------------------ company


def _occupied(company: Company, now: float) -> int:
    """How many burrows beside the sentinel have a meerkat up at `now`."""
    frame = company.frame(now)
    cell = len(mob.frames(mob.COMPANY_FACTOR)[0][0])
    return sum(1 for burrow in range(company.burrows)
               if any(row[cell + 2 + burrow * (cell + 2):cell + 2 + (burrow + 1) * (cell + 2)]
                      .strip(".") for row in frame))


def test_the_company_is_the_sentinel_and_the_burrows_that_fit():
    assert Company(120).burrows == 4
    assert Company(30).burrows == 0
    company = Company(120, started=0.0)
    assert len(company.frame(0.0)[-1]) == len(mob.frames(2)[0][0]) * 5 + 8


def test_meerkats_arrive_and_leave_for_as_long_as_it_runs():
    company = Company(120, started=0.0)
    counts = [_occupied(company, t / 10) for t in range(0, 600)]
    assert counts[0] == 0, "at first the sentinel is alone"
    assert max(counts) >= 2
    changes = sum(1 for a, b in zip(counts, counts[1:], strict=False) if a != b)
    assert changes >= 10, "they keep coming and going"
    assert counts[-100:] != [counts[-1]] * 100, "still moving after a minute"


def test_they_never_all_arrive_at_once():
    company = Company(120, started=0.0)
    first = []
    for burrow in range(company.burrows):
        t = next(x / 20 for x in range(2000)
                 if company._visitor(burrow, x / 20)[0] is not None)
        first.append(t)
    assert len(set(first)) == len(first)


def test_the_sentinel_never_leaves():
    company = Company(120, started=0.0)
    cell = len(mob.frames(2)[0][0])
    for t in [x / 4 for x in range(200)]:
        assert any(row[:cell].strip(".") for row in company.frame(t))


def test_the_same_moment_draws_the_same_frame():
    assert Company(120, started=0.0).frame(17.3) == Company(120, started=0.0).frame(17.3)


def test_without_animation_the_sentinel_keeps_watch_alone():
    company = Company(120, animate=False, started=0.0)
    assert {company.frame(t) for t in (0.0, 5.0, 30.0)} == {company.frame(0.0)}
    assert _occupied(company, 30.0) == 0


# -------------------------------------------------------------------- stage


def test_without_a_terminal_the_audit_has_no_company():
    console = Console(file=io.StringIO(), width=120, height=40)
    with mob.audit_company(console):
        assert mob.attach(object()) is False
        console.print("plain")
    assert console.file.getvalue() == "plain\n"


def test_a_terminal_too_short_gets_no_company():
    console = _terminal(height=18)
    with mob.audit_company(console):
        assert mob.attach(object()) is False


def test_switching_animation_off_removes_the_company(monkeypatch):
    monkeypatch.setenv("OPAAS_NO_ANIMATION", "1")
    with mob.audit_company(_terminal()):
        assert mob.attach(object()) is False


def test_what_the_audit_prints_is_kept_and_printed_in_full_after_it():
    """On the alternate screen nothing scrolls; the record of the run is unchanged."""
    console, errors = _terminal(), _terminal()
    with mob.audit_company(console, "opaas run", also=errors):
        console.print("first")
        errors.print("an error")
        console.print("second")
    out = console.file.getvalue()
    after = out.split("\x1b[?1049l")[-1]
    assert after.index("first") < after.index("second")
    assert "an error" in errors.file.getvalue()
    assert "\x1b[?1049h" in out, "the company is drawn on the alternate screen"


def test_nested_audits_share_the_outer_company():
    console = _terminal()
    with mob.audit_company(console, "opaas audit run"):
        outer = mob._STAGE
        with mob.audit_company(console, "opaas run"):
            assert mob._STAGE is outer
        assert mob._STAGE is outer
    assert mob._STAGE is None


def test_the_progress_bar_joins_the_company_and_leaves_with_it():
    console = _terminal()
    bar = object()
    with mob.audit_company(console):
        assert mob.attach(bar) is True
        assert bar in mob._STAGE.attached
        mob.detach(bar)
        assert bar not in mob._STAGE.attached


def test_the_company_leaves_even_when_the_audit_fails():
    console = _terminal()
    try:
        with mob.audit_company(console):
            console.print("before the failure")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert mob._STAGE is None
    assert "before the failure" in console.file.getvalue().split("\x1b[?1049l")[-1]


def test_the_eyes_glint_survives_the_reduction():
    """One white pixel always loses its block; a meerkat without it looks blind."""
    assert "S" in "".join(mob.frames(2)[0])
    assert "S" not in "".join(mob.frames(2)[1]), "but a closed eye has none"
