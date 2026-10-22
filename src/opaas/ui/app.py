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
        from opaas.cli.banner import status_lines

        status = [{"mark": mark, "text": text} for mark, text, _ in status_lines()]
        return page(request, "overview.html", runs=views.runs(store)[:8], status=status,
                    jobs=[j.as_dict() for j in jobs.queue()][-8:])

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
        from opaas.system.orchestrate import STEPS
        from opaas.ui.jobs import REROUTED_AGENTS

        return page(request, "new.html", sources=views.scenario_sources(),
                    adapters=views.agent_adapters(), rerouted=sorted(REROUTED_AGENTS),
                    reports=views.stored_reports(store), steps=list(STEPS))

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

    def _path(request: Request, name: str) -> Path | None:
        value = (request.query_params.get(name) or "").strip()
        return Path(value).expanduser() if value else None

    async def sentinel(request: Request) -> Response:
        """Sentinel's traffic and the gate's queue, read where the operator keeps them.

        The proxy is started from a terminal (D46); this page only reads its
        log and the approval queue. Approving stays with the named approver (D50).
        """
        log, queue = _path(request, "log"), _path(request, "queue")
        events = []
        if log is not None and log.is_file():
            lines, _ = views.read_jsonl(log)
            events = [views.sentinel_event(r) for r in lines[-200:]]
        return page(request, "sentinel.html", log=log, queue=queue, events=list(reversed(events)),
                    log_missing=log is not None and not log.is_file(),
                    queue_missing=queue is not None and not queue.is_dir(),
                    pending=views.pending_actions(queue) if queue else [])

    async def sentinel_events(request: Request) -> Response:
        """New events in the proxy's log as they are appended, by server-sent events."""
        log = _path(request, "log")
        if log is None or not log.is_file():
            return PlainTextResponse("No such log.", status_code=404)
        once = request.query_params.get("once") == "1"

        async def stream() -> AsyncIterator[str]:
            _, offset = views.read_jsonl(log)  # the page already shows what is there
            if once:
                offset = 0
            while True:
                lines, offset = views.read_jsonl(log, offset=offset)
                for line in lines:
                    yield f"data: {json.dumps(views.sentinel_event(line))}\n\n"
                if once or await request.is_disconnected():
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def sentinel_pending(request: Request) -> Response:
        queue = _path(request, "queue")
        return JSONResponse(views.pending_actions(queue) if queue else [])

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
        Route("/batch", batch_page),
        Route("/jobs/{job_id}", job_page),
        Route("/api/jobs", api_jobs, methods=["GET", "POST"]),
        Route("/api/jobs/{job_id}", api_job, methods=["GET"]),
        Route("/api/jobs/{job_id}/cancel", api_job, methods=["POST"]),
        Route("/sentinel", sentinel),
        Route("/sentinel/events", sentinel_events),
        Route("/api/sentinel/pending", sentinel_pending),
        Route("/favicon.svg", favicon),
        Route("/mascot.json", mascot),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    middleware = [Middleware(_Guard, token=token,
                             hosts=hosts or {"127.0.0.1", "localhost"})]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.token, app.state.jobs = token, jobs
    return app
