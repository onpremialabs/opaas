"""`opaas ui`: start the local dashboard (D79).

Loopback only: any other address is refused, and remote use goes through an
SSH tunnel (`ssh -L 8780:127.0.0.1:8780 host`). The session token is printed in
the address, and the browser trades it for a cookie on the first request.
"""

from __future__ import annotations

import importlib.util
import secrets
import sys
import webbrowser
from typing import Annotated

import typer

from opaas.cli.common import console, error
from opaas.core.store import runs_dir

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def ui(
    port: Annotated[int, typer.Option(
        "--port", "-p", min=1, max=65535,
        help="Port on 127.0.0.1. Not 8765: smith serves rerouted agents' mock tools there.")]
    = 8780,
    host: Annotated[str, typer.Option(
        "--host", help="Loopback only: 127.0.0.1, localhost or ::1. For another machine, "
        "tunnel with ssh -L.")] = "127.0.0.1",
    open_browser: Annotated[bool | None, typer.Option(
        "--open/--no-open", help="Open the dashboard in the browser. By default only from a "
        "terminal.")] = None,
) -> None:
    """Starts the local dashboard over your run store. It measures nothing itself."""
    if host not in LOOPBACK:
        error(f"[red]{host} is not a loopback address.[/red] The dashboard serves "
              "only this machine. From another one, tunnel to it: "
              f"ssh -L {port}:127.0.0.1:{port} <this-host>.")
        raise typer.Exit(code=2)
    missing = [m for m in ("starlette", "uvicorn", "jinja2") if importlib.util.find_spec(m) is None]
    if missing:
        error(f"[red]The dashboard needs the ui extra[/red] ({', '.join(missing)} "
              "missing). Install it with: uv sync --extra ui, or "
              "pip install \"opaas\\[ui]\".")
        raise typer.Exit(code=1)

    import uvicorn

    from opaas.ui.app import create_app

    store = runs_dir()
    store.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    shown = "[::1]" if host == "::1" else host
    names = {f"{shown}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"}
    app = create_app(store, token=token, hosts=names)
    url = f"http://{shown}:{port}/?token={token}"
    console.print(f"[bold]OPAAS dashboard[/bold] on {shown}:{port}, over {store}")
    console.print(f"  open: {url}")
    console.print("  [dim]the token in that address is this session's key; Ctrl+C stops "
                  "the server (running jobs keep going)[/dim]")
    if open_browser if open_browser is not None else sys.stdout.isatty():
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
