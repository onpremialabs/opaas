"""The run store: every result kept, written once, never replaced (D76).

Before it, a report landed wherever the command happened to run (`./runs`, or
`--output`), and a second run with the same path replaced the first without a
word. A tool whose output is evidence cannot lose its earlier evidence that
way, so every command that produces a result now records it here:
`~/.opaas/runs/<run-id>/`, or `$OPAAS_RUNS_DIR`.

**One directory per run, allocated atomically.** The id is `YYYYMMDD-NNN`, as
before, but it is claimed with an exclusive `mkdir`. Two runs started at once
(the dashboard can do that) never share a directory: the second one simply
takes the next number. Checking for a free name and then creating it, as
`allocate_run_id` does, is not enough when two processes check together.

**Written once.** Files enter a run through `write_bytes` or `keep`, which
open them exclusively for creation, and closing a run makes every file in it
read-only. `run.json` is the one file written twice: once when the run opens
and once when it closes, never after.

**`run.json` is the contract with whatever reads the store** (the dashboard,
`docs/dashboard.md` §4). It says what ran, with which OPAAS, when, how it
ended, and the hash of every file the run wrote. A field the producer did not
have is absent, never defaulted: a run that talked to no endpoint has no
`endpoint`.

**Nothing here deletes.** Removing a run is done by hand, outside OPAAS.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opaas.core.hashing import file_digest

#: Version of the `run.json` layout. A reader refuses one it does not know.
RUN_FORMAT = 1
RUN_FILE = "run.json"

#: What a run may say about how it ended. `interrupted` is never written by a
#: run: a reader concludes it from a `running` run whose process is gone.
RUN_KINDS = frozenset(
    {"bench", "smith", "audit", "discover", "inquest", "swarm", "sentinel", "card", "compare",
     "certificate"}
)
FINAL_STATUSES = frozenset({"completed", "partial", "failed"})

_MAX_RUNS_PER_DAY = 999

#: The operational log a running command appends to, and the dashboard follows
#: (`docs/dashboard.md` §4). It is not evidence: not chained, not signed, never
#: cited by a report.
PROGRESS_FILE = "progress.jsonl"
PROGRESS_FORMAT = 1
#: The only fields a progress line may carry (N4: never content).
PROGRESS_FIELDS = frozenset({"t", "kind", "phase", "index", "total", "percent", "message"})
_MAX_MESSAGE = 200
_URL_QUERY = re.compile(r"(\w+://[^\s?#]*)[?#][^\s]*")
_URL_CREDENTIALS = re.compile(r"(\w+://)[^\s/@]+@")


class StoreError(RuntimeError):
    """The store refused an operation: a full day, a closed run, a file already there."""


def runs_dir() -> Path:
    """Where runs are kept: `$OPAAS_RUNS_DIR`, or `~/.opaas/runs`."""
    override = os.environ.get("OPAAS_RUNS_DIR")
    return Path(override).expanduser() if override else Path.home() / ".opaas" / "runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def allocate_run_dir(root: Path | None = None, now: datetime | None = None) -> Path:
    """Claims the next free `YYYYMMDD-NNN` directory with an exclusive `mkdir`."""
    root = root or runs_dir()
    root.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    for index in range(1, _MAX_RUNS_PER_DAY + 1):
        candidate = root / f"{stamp}-{index:03d}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise StoreError(
        f"More than {_MAX_RUNS_PER_DAY} runs on {stamp} in {root}. Point OPAAS_RUNS_DIR at "
        "another directory to keep going."
    )


def _write_json_once(path: Path, document: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def _make_read_only(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


@dataclass(slots=True)
class Run:
    """An open run: the directory it writes into, until `close`."""

    directory: Path
    record: dict[str, Any]
    closed: bool = False

    @property
    def run_id(self) -> str:
        return self.directory.name

    def path(self, name: str) -> Path:
        """Where a file of this run goes. Names stay inside the run directory."""
        target = (self.directory / name).resolve()
        if self.directory.resolve() not in target.parents:
            raise StoreError(f"{name!r} is outside the run directory.")
        return target

    def write_bytes(self, name: str, data: bytes) -> Path:
        """Writes a new file into the run; one that exists is never replaced."""
        self._open_for_writing()
        if name in (RUN_FILE, PROGRESS_FILE):
            raise StoreError(f"{name} is the run's own record: it is not written as a file.")
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            raise StoreError(
                f"{target} already exists in run {self.run_id}: a run's files are written once."
            ) from None
        return target

    def append_progress(self, **fields: Any) -> None:
        """Appends one line to the run's progress log, while the run is open.

        The one file of a run that grows. Fields outside `PROGRESS_FIELDS` are
        refused, and the message is cut short and stripped of URL credentials
        and query strings: a progress line says where a command is, never what
        it read or wrote (N4).
        """
        self._open_for_writing()
        unknown = set(fields) - PROGRESS_FIELDS
        if unknown:
            raise StoreError(f"Progress lines carry no {sorted(unknown)}: only "
                             f"{sorted(PROGRESS_FIELDS)}.")
        line = {"t": _now(), **{k: v for k, v in fields.items() if v is not None}}
        if "message" in line:
            message = _URL_CREDENTIALS.sub(r"\1", str(line["message"]))
            line["message"] = _URL_QUERY.sub(r"\1", message)[:_MAX_MESSAGE]
        if isinstance(line.get("percent"), float):
            line["percent"] = round(line["percent"], 1)
        with (self.directory / PROGRESS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    def keep(self, source: Path, name: str | None = None) -> Path:
        """Copies a file or a directory the command wrote elsewhere into the run.

        The command's own output (`--output`) stays where the user asked for it;
        the store keeps its copy, which a later run writing the same path cannot
        touch.
        """
        self._open_for_writing()
        name = name or source.name
        target = self.path(name)
        if source.resolve() == target or self.directory.resolve() in source.resolve().parents:
            # Already written inside the run: nothing to copy.
            return source
        if source.is_dir():
            for item in sorted(source.rglob("*")):
                if item.is_file():
                    relative = item.relative_to(source).as_posix()
                    self.write_bytes(f"{name}/{relative}", item.read_bytes())
        else:
            self.write_bytes(name, source.read_bytes())
        return target

    def close(self, status: str, **fields: Any) -> dict[str, Any]:
        """Ends the run: its status, its files' hashes, and every file read-only."""
        self._open_for_writing()
        if status not in FINAL_STATUSES:
            raise StoreError(f"Unknown final status {status!r}: one of {sorted(FINAL_STATUSES)}.")
        if (self.directory / PROGRESS_FILE).is_file():
            # A reader following the log learns the run ended without polling run.json.
            self.append_progress(kind="end", message=f"ended {status}")
        files = {
            str(item.relative_to(self.directory)).replace(os.sep, "/"): file_digest(item)
            for item in sorted(self.directory.rglob("*"))
            if item.is_file() and item.name not in (RUN_FILE, PROGRESS_FILE)
        }
        # `fields` may carry an `endedAt` of their own: an imported run ended long ago.
        record = {**self.record, "endedAt": _now(), **fields, "status": status, "files": files}
        record.pop("pid", None)
        # The second and last write of run.json: through a temporary file and a
        # replace, so a reader never sees half of it.
        temporary = self.directory / f".{RUN_FILE}.closing"
        _write_json_once(temporary, record)
        os.replace(temporary, self.directory / RUN_FILE)
        for item in self.directory.rglob("*"):
            if item.is_file():
                _make_read_only(item)
        self.record = record
        self.closed = True
        return record

    def _open_for_writing(self) -> None:
        if self.closed:
            raise StoreError(f"Run {self.run_id} is closed: nothing more is written into it.")


def open_run(
    kind: str,
    *,
    command: list[str] | None,
    opaas_version: str,
    spec_version: str,
    endpoint: str | None = None,
    job_id: str | None = None,
    root: Path | None = None,
) -> Run:
    """Opens a run: claims its directory and writes the first `run.json`.

    `job_id` is set when the dashboard launched the command (D79): it is how
    the dashboard finds the run its job produced while it is still running.
    """
    if kind not in RUN_KINDS:
        raise StoreError(f"Unknown run kind {kind!r}: one of {sorted(RUN_KINDS)}.")
    directory = allocate_run_dir(root)
    record: dict[str, Any] = {
        "format": RUN_FORMAT,
        "runId": directory.name,
        "kind": kind,
        "opaasVersion": opaas_version,
        "specVersion": spec_version,
    }
    if command is not None:
        record["command"] = command
    if endpoint is not None:
        record["endpoint"] = endpoint
    if job_id is not None:
        record["jobId"] = job_id
    record.update({"startedAt": _now(), "status": "running", "pid": os.getpid()})
    _write_json_once(directory / RUN_FILE, record)
    return Run(directory=directory, record=record)


def load_run(directory: Path) -> dict[str, Any]:
    """Reads a run's record, concluding `interrupted` for a run whose process is gone."""
    path = directory / RUN_FILE
    if not path.is_file():
        raise StoreError(f"{directory} has no {RUN_FILE}: it is not a recorded run.")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("format") != RUN_FORMAT:
        raise StoreError(
            f"{path} has format {record.get('format')!r}; this OPAAS reads format "
            f"{RUN_FORMAT}. Upgrade OPAAS to read it."
        )
    if record.get("status") == "running" and not _process_alive(record.get("pid")):
        record = {**record, "status": "interrupted"}
    return record


def list_runs(root: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded run, oldest first. Directories without a record are skipped."""
    root = root or runs_dir()
    if not root.is_dir():
        return []
    runs = []
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and (directory / RUN_FILE).is_file():
            try:
                runs.append(load_run(directory))
            except (StoreError, ValueError):
                continue
    return runs


def verify_files(directory: Path) -> dict[str, str]:
    """Each file the record lists: `ok`, `modified` or `missing`."""
    record = load_run(directory)
    result = {}
    for name, digest in record.get("files", {}).items():
        path = directory / name
        if not path.is_file():
            result[name] = "missing"
        else:
            result[name] = "ok" if file_digest(path) == digest else "modified"
    return result


def import_directory(
    source: Path,
    *,
    kind: str,
    opaas_version: str,
    spec_version: str,
    fields: dict[str, Any],
    status: str,
    root: Path | None = None,
) -> Run:
    """Copies an old run directory into the store as a new, closed run.

    The source is left where it is: importing never moves anything. The
    command that produced it is not known, so the record has none, and says
    where the files came from instead.
    """
    run = open_run(kind, command=None, opaas_version=opaas_version, spec_version=spec_version,
                   root=root)
    for item in sorted(source.rglob("*")):
        if item.is_file():
            run.write_bytes(str(item.relative_to(source)), item.read_bytes())
    run.close(status, importedFrom=str(source.resolve()), **fields)
    return run


def read_progress(directory: Path, *, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """The progress lines from byte `offset` on, and the offset to read from next.

    A reader that follows a running command calls it again with the offset it
    got back; a half-written last line is left for the next call.
    """
    path = directory / PROGRESS_FILE
    if not path.is_file():
        return [], offset
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    complete = data[: data.rfind(b"\n") + 1]
    lines = []
    for raw in complete.splitlines():
        try:
            lines.append(json.loads(raw.decode("utf-8")))
        except ValueError:
            continue
    return lines, offset + len(complete)


def _process_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a runtime dependency
        return True
    return psutil.pid_exists(pid)
