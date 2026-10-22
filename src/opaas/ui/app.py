"""`opaas ui`: the local dashboard server (D75, D79).

It reads the run store and launches `opaas` commands; it measures nothing
(`docs/dashboard.md` §2). Everything it shows is built from the files under
`~/.opaas/runs` by `ui.views`, and everything it starts is a job in
`ui.jobs`, shown as the exact command line before it runs.

**Local, and narrowed** (`dashboard.md` §6):

- It binds loopback only, and the CLI refuses any other address.
- A random session token is printed in the start URL. The first request
  trades it for an `HttpOnly`, `SameSite=Strict` cookie; any request with
  neither is refused.
- `Host` must be the address the server was started on: a page served from
  another name, through DNS rebinding, is turned away.
- Every POST carries the token in a header as well. There is no CORS, and no
  GET request changes anything.
- Pages may load nothing from elsewhere (a strict Content-Security-Policy).
  Files from a run (an audit certificate) are served sandboxed, scripts off.

Starlette and Jinja2 come with the `ui` extra and are imported only here.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from opaas import OPAAS_SPEC_VERSION, __version__
from opaas.core.store import StoreError, load_run, read_progress
from opaas.ui import views
from opaas.ui.jobs import JobError, JobManager

HERE = Path(__file__).parent
COOKIE = "opaas_session"
HEADER = "x-opaas-token"

_PAGE_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
#: A run's files (an audit certificate, a report) render as documents, never as code.
_FILE_CSP = ("sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
             "frame-ancestors 'self'")


class _Guard(BaseHTTPMiddleware):
    """Token, Host and headers, before any route runs."""

    def __init__(self, app: Any, *, token: str, hosts: set[str]) -> None:
        super().__init__(app)
        self.token, self.hosts = token, hosts

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.headers.get("host", "") not in self.hosts:
            return PlainTextResponse("Unknown host: open the address `opaas ui` printed.",
                                     status_code=400)
        offered = request.query_params.get("token")
        if offered is not None:
            if not hmac.compare_digest(offered, self.token):
                return PlainTextResponse("Wrong token.", status_code=403)
            # Trade the token in the URL for a cookie, and take it out of the address bar.
            response: Response = RedirectResponse(request.url.path, status_code=303)
            response.set_cookie(COOKIE, self.token, httponly=True, samesite="strict",
                                path="/")
            return response
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), self.token):
            return PlainTextResponse("Open the address `opaas ui` printed in your terminal: "
                                     "it carries this session's token.", status_code=403)
        if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
                request.headers.get(HEADER, ""), self.token):
            return PlainTextResponse("Missing session header.", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _PAGE_CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app(store: Path, *, token: str | None = None, hosts: set[str] | None = None,
               jobs: JobManager | None = None) -> Starlette:
    """The dashboard over `store`. `hosts`: the `Host` values accepted."""
    token = token or secrets.token_urlsafe(24)
    jobs = jobs or JobManager(store=store)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(version=__version__, spec=OPAAS_SPEC_VERSION,
                                 store=str(store), token=token)

    def page(request: Request, name: str, **context: Any) -> Response:
        return templates.TemplateResponse(request, name, context)

    async def overview(request: Request) -> Response:
        return page(request, "overview.html", runs=views.runs(store)[:8],
                    jobs=[j.as_dict() for j in jobs.queue()][-8:], about=views.about())

    async def runs_page(request: Request) -> Response:
        every = views.runs(store)
        query = {name: request.query_params.get(name, "") for name in views.FILTERS}
        return page(request, "runs.html", runs=views.filter_runs(every, query), query=query,
                    total=len(every),
                    statuses=sorted({str(r.get("status")) for r in every}),
                    versions=sorted({str(r.get("opaasVersion")) for r in every}))

    async def run_detail(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        try:
            data = views.run_page(store, run_id)
        except (StoreError, ValueError):
            return page(request, "missing.html", run_id=run_id)
        return page(request, "run.html", **data)

    async def run_file(request: Request) -> Response:
        directory = (store / request.path_params["run_id"]).resolve()
        target = (directory / request.path_params["path"]).resolve()
        if directory not in target.parents or not target.is_file():
            return PlainTextResponse("No such file in this run.", status_code=404)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".json", ".jsonl", ".log"):
            kind = "text/plain; charset=utf-8"
        headers = {"Content-Security-Policy": _FILE_CSP}
        if request.query_params.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
        return Response(target.read_bytes(), media_type=kind, headers=headers)

    async def run_events(request: Request) -> Response:
        """Server-sent events: the run's progress lines as they arrive, then its end."""
        directory = store / request.path_params["run_id"]

        async def stream() -> AsyncIterator[str]:
            offset = 0
            while True:
                lines, offset = read_progress(directory, offset=offset)
                for line in lines:
                    yield f"data: {json.dumps(line)}\n\n"
                try:
                    status = load_run(directory).get("status")
                except (StoreError, ValueError):
                    status = "missing"
                if status != "running":
                    lines, offset = read_progress(directory, offset=offset)
                    for line in lines:
                        yield f"data: {json.dumps(line)}\n\n"
                    yield f"event: status\ndata: {json.dumps({'status': status})}\n\n"
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def new_job(request: Request) -> Response:
        # Every job runs in its own directory: the manifest the draft writes and the
        # audit reads must be named absolutely, so the default is where `opaas ui` started.
        return page(request, "new.html", manifest=str(Path.cwd() / "system.json"),
                    cwd=str(Path.cwd()))

    async def folder_pick(request: Request) -> Response:
        """The system's own folder dialog, on this desktop; the page falls back to its list."""
        from opaas.ui import dialog

        try:
            body = await request.json()
        except ValueError:
            body = {}
        initial = str((body or {}).get("path") or Path.cwd())
        try:
            chosen = await asyncio.to_thread(dialog.pick_folder, initial)
        except dialog.DialogError as exc:
            return JSONResponse({"error": f"No folder dialog here: {exc}"}, status_code=501)
        return JSONResponse({"path": chosen} if chosen else {"cancelled": True})

    async def folder_list(request: Request) -> Response:
        """One level of the folder picker; the environment is where `opaas ui` started."""
        listing = views.folders(Path(request.query_params.get("path") or Path.cwd()))
        if listing is None:
            return JSONResponse({"error": "Not a folder."}, status_code=404)
        return JSONResponse(listing)

    async def discover_latest(request: Request) -> Response:
        return JSONResponse(views.latest_discover(store) or {"runId": None})

    async def discover_run(request: Request) -> Response:
        picker = views.discover_picker(store, request.path_params["run_id"])
        if picker is None:
            return JSONResponse({"error": "No inventory for this run."}, status_code=404)
        return JSONResponse(picker)

    async def batch_page(request: Request) -> Response:
        ids = [i for i in (request.query_params.get("ids") or "").split(",") if i]
        return page(request, "batch.html", ids=ids)

    async def job_page(request: Request) -> Response:
        job = jobs.jobs.get(request.path_params["job_id"])
        if job is None:
            return page(request, "missing.html", run_id=request.path_params["job_id"])
        return page(request, "job.html", job=job.as_dict())

    async def api_jobs(request: Request) -> Response:
        if request.method == "GET":
            return JSONResponse([j.as_dict() for j in jobs.queue()])
        try:
            body = await request.json()
            args = [str(a) for a in body.get("args", [])]
            job = jobs.submit(args, confirmed_rerouted=bool(body.get("confirmRerouted")))
        except JobError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (ValueError, AttributeError):
            return JSONResponse({"error": "Send {\"args\": [...]}."}, status_code=400)
        return JSONResponse(job.as_dict(), status_code=201)

    async def api_job(request: Request) -> Response:
        job = jobs.jobs.get(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        if request.method == "POST":
            job = jobs.cancel(job.id)
        run = views.run_for_job(store, job.id)
        log = ""
        if job.log and job.log.is_file():
            log = job.log.read_bytes()[-4000:].decode("utf-8", "replace")
        report = views.report_name(store / run["runId"], run) if run else None
        steps = views.audit_live_steps(store, run) if run else None
        return JSONResponse({**job.as_dict(), "run": run, "log": log, "reportFile": report,
                             "steps": steps})

    async def favicon(request: Request) -> Response:
        return Response(views.head_svg(), media_type="image/svg+xml")

    async def mascot(request: Request) -> Response:
        return JSONResponse(views.meerkat())

    routes = [
        Route("/", overview),
        Route("/runs", runs_page),
        Route("/runs/{run_id}", run_detail),
        Route("/runs/{run_id}/events", run_events),
        Route("/runs/{run_id}/files/{path:path}", run_file),
        Route("/new", new_job),
        Route("/api/discover/latest", discover_latest),
        Route("/api/discover/{run_id}", discover_run),
        Route("/api/folders", folder_list),
        Route("/api/folders/pick", folder_pick, methods=["POST"]),
        Route("/batch", batch_page),
        Route("/jobs/{job_id}", job_page),
        Route("/api/jobs", api_jobs, methods=["GET", "POST"]),
        Route("/api/jobs/{job_id}", api_job, methods=["GET"]),
        Route("/api/jobs/{job_id}/cancel", api_job, methods=["POST"]),
        Route("/favicon.svg", favicon),
        Route("/mascot.json", mascot),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    middleware = [Middleware(_Guard, token=token,
                             hosts=hosts or {"127.0.0.1", "localhost"})]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.token, app.state.jobs = token, jobs
    return app
