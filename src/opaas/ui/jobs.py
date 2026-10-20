"""Jobs: the CLI commands the dashboard launches, one queue per endpoint (D79).

The dashboard measures nothing itself (D75). A job is an `opaas …` command
line, run as a child process exactly as shown to the user, which records its
own run in the store. The server only starts it, waits for it, and links the
job to the run it produced through `OPAAS_JOB_ID`, which `recorded_run`
copies into `run.json`.

**One queue per endpoint.** Two measurements against one engine at once
contaminate each other's latency, throughput and concurrency, so jobs that
name the same engine or agent run one after another, in order. Jobs that talk
to no endpoint run alongside.

**Only measuring commands, never a key.** The allowlist below is what the
dashboard may launch. Signing, key generation, delegation, revocation, the
sentinel proxy and approvals stay in a terminal (`docs/dashboard.md` §5).
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: (command, subcommand) pairs the dashboard may launch. `None`: no subcommand.
ALLOWED: frozenset[tuple[str, str | None]] = frozenset({
    ("run", None),
    ("compare", None),
    ("certificate", None),
    ("discover", "scan"),
    ("discover", "gaps"),
    ("smith", "run"),
    ("audit", "run"),
    ("audit", "draft"),
    ("audit", "validate"),
    ("inquest", "audit"),
    ("swarm", "analyze"),
    ("card", "build"),
    ("card", "manifest"),
    ("sentinel", "overhead"),
})
_WITH_SUBCOMMAND = {command for command, sub in ALLOWED if sub is not None}

#: Agents whose tools must already point at OPAAS's mock MCP server (D68).
REROUTED_AGENTS = frozenset({"a2a", "dify", "flowise", "langgraph", "langserve", "n8n",
                             "open-webui"})

JOBS_DIR = ".jobs"
JOB_ENV = "OPAAS_JOB_ID"


class JobError(ValueError):
    """A job the dashboard refuses to launch, with what to do instead."""


def check_command(args: Sequence[str], *, confirmed_rerouted: bool = False) -> None:
    """Refuses anything outside the allowlist, before a process exists."""
    if not args:
        raise JobError("An empty command launches nothing.")
    command = args[0]
    sub = args[1] if command in _WITH_SUBCOMMAND and len(args) > 1 else None
    if (command, sub) not in ALLOWED:
        name = f"{command} {sub}" if sub else command
        raise JobError(f"`opaas {name}` is not launched from the dashboard. Run it in a "
                       "terminal: signing, keys, the sentinel proxy and approvals stay there.")
    if command == "certificate" and "--run" not in args:
        raise JobError("The dashboard renders only a run's audit certificate: "
                       "`certificate --run <id>`.")
    if command == "smith" and _option(args, "--agent", "-a") in REROUTED_AGENTS \
            and not confirmed_rerouted:
        raise JobError("This agent's tools must point at OPAAS's mock MCP server before the "
                       "run, or its scenarios act on real tools (D68). Confirm it to go on.")


def endpoint_of(args: Sequence[str]) -> str | None:
    """The engine or agent a command talks to: its queue. None for none."""
    explicit = _option(args, "--base-url") or _option(args, "--endpoint")
    if explicit:
        return explicit.rstrip("/")
    if args and args[0] in ("run", "smith"):
        backend = _option(args, "--backend", "-b") or ("vllm" if args[0] == "run" else None)
        if backend:
            from opaas.adapters import get_backend
            from opaas.adapters.base import BackendError

            try:
                return getattr(get_backend(backend), "default_base_url", None)
            except BackendError:
                return f"backend:{backend}"
    return None


def _option(args: Sequence[str], *names: str) -> str | None:
    for index, word in enumerate(args):
        for name in names:
            if word == name and index + 1 < len(args):
                return args[index + 1]
            if word.startswith(name + "="):
                return word.split("=", 1)[1]
    return None


@dataclass(slots=True)
class Job:
    id: str
    args: list[str]
    endpoint: str | None
    created: float
    status: str = "queued"  # queued, running, finished, cancelled, failed-to-start
    exit_code: int | None = None
    started: float | None = None
    ended: float | None = None
    process: Any = None
    log: Path | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "args": self.args, "command": ["opaas", *self.args],
                "endpoint": self.endpoint, "status": self.status, "exitCode": self.exit_code,
                "created": self.created, "started": self.started, "ended": self.ended,
                "error": self.error}


#: Starts a job's process: (argv, cwd, env, log file) -> a Popen-like object.
Launcher = Callable[[list[str], Path, dict[str, str], Path], Any]


def _launch(argv: list[str], cwd: Path, env: dict[str, str], log: Path) -> Any:
    handle = log.open("ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=handle,
                            stderr=subprocess.STDOUT, creationflags=flags)


@dataclass
class JobManager:
    """Queues, starts and watches jobs. Its state is in memory; runs are on disk."""

    store: Path
    launcher: Launcher = _launch
    jobs: dict[str, Job] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def submit(self, args: Sequence[str], *, confirmed_rerouted: bool = False) -> Job:
        check_command(args, confirmed_rerouted=confirmed_rerouted)
        job = Job(id=secrets.token_hex(6), args=list(args), endpoint=endpoint_of(args),
                  created=time.time())
        with self._lock:
            self.jobs[job.id] = job
        self._pump()
        return job

    def cancel(self, job_id: str) -> Job:
        """A queued job is dropped; a running one is stopped. Its run reads interrupted."""
        job = self.jobs[job_id]
        with self._lock:
            if job.status == "queued":
                job.status, job.ended = "cancelled", time.time()
            elif job.status == "running" and job.process is not None:
                job.process.terminate()
                job.status = "cancelled"
        self._pump()
        return job

    def queue(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created)

    def _busy(self, endpoint: str | None) -> bool:
        return endpoint is not None and any(
            j.status == "running" and j.endpoint == endpoint for j in self.jobs.values())

    def _pump(self) -> None:
        """Starts every queued job whose endpoint is free, oldest first."""
        with self._lock:
            starting = []
            claimed: set[str] = set()
            for job in self.queue():
                if job.status != "queued":
                    continue
                if job.endpoint is not None and (self._busy(job.endpoint)
                                                 or job.endpoint in claimed):
                    continue
                if job.endpoint is not None:
                    claimed.add(job.endpoint)
                job.status = "running"
                starting.append(job)
        for job in starting:
            self._start(job)

    def _start(self, job: Job) -> None:
        workdir = self.store / JOBS_DIR / job.id
        workdir.mkdir(parents=True, exist_ok=True)
        job.log = workdir / "job.log"
        # The job writes to a file, never a terminal: TTY_COMPATIBLE=0 makes Rich print
        # plain lines, even when the server's own environment forces colour.
        env = {k: v for k, v in os.environ.items() if k != "FORCE_COLOR"}
        env.update({"OPAAS_RUNS_DIR": str(self.store), JOB_ENV: job.id,
                    "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "TTY_COMPATIBLE": "0"})
        try:
            job.process = self.launcher([sys.executable, "-m", "opaas", *job.args],
                                        workdir, env, job.log)
        except OSError as exc:
            job.status, job.error, job.ended = "failed-to-start", str(exc), time.time()
            self._pump()
            return
        job.started = time.time()
        threading.Thread(target=self._wait, args=(job,), daemon=True).start()

    def _wait(self, job: Job) -> None:
        code = job.process.wait()
        with self._lock:
            job.exit_code = code
            job.ended = time.time()
            if job.status == "running":
                job.status = "finished"
        self._pump()
