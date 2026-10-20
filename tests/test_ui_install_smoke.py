"""`scripts/smoke_ui_install.py`, the dashboard's flow from an installed `opaas[ui]` (phase 24).

CI runs the script from a wheel installed into an empty environment, which is
the point of it. Here it runs from the source tree, so a change to a page or a
command that breaks the script is caught by `pytest`, not by the release.
Its jobs are real child processes of `python -m opaas` (a draft and a
comparison: neither needs a backend); nothing reaches the network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_ui_install.py"


def _script():
    spec = importlib.util.spec_from_file_location("smoke_ui_install", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dashboards_flow_runs_end_to_end_against_the_doubles(tmp_path, capsys):
    assert _script().main(["--workdir", str(tmp_path)]) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "a comparison job records the result of opaas compare" in out
    assert "signing is refused" in out


def test_installed_mode_refuses_the_source_tree(tmp_path, capsys):
    """CI must not pass by importing the checkout instead of the wheel."""
    assert _script().main(["--installed", "--workdir", str(tmp_path)]) == 1
    assert "source tree" in capsys.readouterr().err
