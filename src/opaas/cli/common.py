"""What every command group shares: the consoles, plain printing, the signing key.

Kept apart from `main` so the module command groups (`discover`, `smith`,
`sentinel`, `inquest`, `swarm`, `card`, `evidence`) can use them without
importing the whole bench CLI.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

import typer
from rich.console import Console
from rich.markup import escape

from opaas import OPAAS_SPEC_VERSION, __version__
from opaas.core.credential import (
    Pkcs11Signer,
    Signer,
    TrustRoot,
    find_revocation_lists,
    load_private_key,
    resolve_trust,
)
from opaas.core.credential.keys import DID_DOCUMENT_FILENAME, public_key_multibase
from opaas.core.store import Run, StoreError, open_run

# emoji=False: Rich turns ":key:" into a key emoji, so "did:key:z6Mk…" printed
# as "did🔑z6Mk…". A tool that prints identifiers must print them verbatim.
console = Console(emoji=False)
err_console = Console(stderr=True, emoji=False)


def _load_signing_key(key: Path, passphrase: str | None) -> Any:
    """Loads the key, asking for the passphrase at the keyboard if needed.

    Signing happens many times, so the passphrase would end up in the shell
    history if `--passphrase` were the only way.
    """
    try:
        return load_private_key(key, passphrase)
    except TypeError:
        if passphrase is not None or not sys.stdin.isatty():
            raise
    return load_private_key(key, getpass.getpass(f"Passphrase for {key.name}: "))


def _signing_identity(key: Path, private_key: Any, issuer: str | None) -> tuple[str, str]:
    """Which name a credential is signed under, and with which verification method (D39).

    The name comes from the DID document keygen wrote beside the key, unless
    `--issuer` says otherwise, and then the two must agree. Signing under a
    name whose document does not list this key produces a credential nobody
    can verify against that name; signing under someone else's name by
    default is how every test key used to claim to be onpremIALabs.
    """
    document_path = key.parent / DID_DOCUMENT_FILENAME
    document = None
    if document_path.is_file():
        try:
            document = json.loads(document_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{document_path} is not valid JSON: {exc}") from None

    if document is None:
        if issuer is None:
            raise ValueError(
                f"No --issuer and no {DID_DOCUMENT_FILENAME} next to {key.name}: there is no "
                "name to sign under. Keep the did.json keygen wrote beside the key, or pass "
                "--issuer with a DID whose published document lists this key."
            )
        return issuer, f"{issuer}#key-1"

    named = str(document.get("id") or "")
    if issuer is not None and issuer != named:
        raise ValueError(
            f"{document_path} names this key {named!r}, not {issuer!r}. A credential signed "
            f"as {issuer!r} could not be verified against that issuer's document. Use "
            f"--issuer {named}, or the key that belongs to {issuer!r}."
        )
    methods = document.get("verificationMethod") or []
    own = public_key_multibase(private_key.public_key())
    for method in methods:
        if method.get("publicKeyMultibase") == own:
            return named, str(method.get("id"))
    raise ValueError(
        f"{document_path} does not list the key in {key.name}: it belongs to another key "
        "pair. Signing would produce a credential its own issuer document cannot verify."
    )


def _open_root_signer(
    *,
    key: Path | None,
    passphrase: str | None,
    pkcs11_module: str | None,
    key_label: str | None,
    token_label: str | None,
) -> Signer:
    """The root's signer: a key file, or a PKCS#11 token that never lets the key out (D65).

    The PIN is read from `OPAAS_TOKEN_PIN` or asked at the keyboard, never
    taken as an argument, for the same reason as the passphrase.
    """
    if (key is None) == (pkcs11_module is None):
        raise ValueError(
            "Name exactly one root signer: --root-key <file.pem>, or --pkcs11-module <module> "
            "with --key-label for a hardware token."
        )
    if key is not None:
        return _load_signing_key(key, passphrase)
    if not key_label:
        raise ValueError("--pkcs11-module needs --key-label: the label of the key on the token.")
    pin = os.environ.get("OPAAS_TOKEN_PIN")
    if pin is None:
        if not sys.stdin.isatty():
            raise ValueError(
                "No terminal to ask for the token PIN: set OPAAS_TOKEN_PIN, or run it by hand."
            )
        pin = getpass.getpass("Token PIN: ")
    return Pkcs11Signer(
        module=str(pkcs11_module), key_label=key_label, pin=pin, token_label=token_label
    ).open()


def _verification_trust(
    credential: dict[str, Any],
    *,
    key: Path | None,
    did_document: Path | None,
    revocations: Path | None = None,
) -> tuple[TrustRoot, list[dict[str, Any]], list[str]]:
    """The root to verify from and the revocation lists held for its issuer.

    Both come from the verifier's side (D4, D65): `--key` / `--did-document`,
    the installed anchors, the bundled ones. Never from the credential.
    Raises `TrustAnchorError` or `ValueError` saying what is missing.
    """
    issuer = str(credential.get("issuer", ""))
    root = resolve_trust(issuer, key_path=key, did_document_path=did_document)
    found = find_revocation_lists(issuer, path=revocations)
    return root, [document for document, _ in found], [origin for _, origin in found]


def _plain(value: Any) -> str:
    """Report text, never read as Rich markup.

    A limitation that begins with '[faithfulness]' is a label, not a style: printed
    as-is, Rich swallows it and the line comes out missing the word that said which
    dimension went unmeasured. In a tool that promises to omit nothing, the renderer
    eating part of a limitation is worse than the limitation itself. It also means a
    third party's report cannot break `show` with a stray bracket.
    """
    return escape(str(value))


def write_json(path: Path, document: dict[str, Any]) -> None:
    """For results that are not signed evidence: readable, indented JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


# --- The run store (D76) ---------------------------------------------------

Force = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Write over an existing output path. The run store keeps its own copy of every "
        "earlier result regardless.",
    ),
]

#: Words that make a parameter secret: its value never reaches `run.json`. The
#: report is public (invariant 12), and so is the record of how it was produced.
#: Whole words of the name, not substrings: `max_tokens` is not a token.
_SECRET_WORDS = frozenset({"passphrase", "password", "secret", "pin", "token"})


def refuse_overwrite(path: Path | None, force: bool) -> None:
    """An existing output is not replaced unless the user says so (D76).

    A directory counts as existing only when it holds something: `-o out/`
    on an empty directory overwrites nothing.
    """
    if path is None or force:
        return
    occupied = path.is_file() or (path.is_dir() and any(path.iterdir()))
    if occupied:
        err_console.print(
            f"[red]{path} already exists.[/red] A result is never replaced without asking: "
            "choose another path, or pass --force (the run store keeps the earlier one)."
        )
        raise typer.Exit(code=2)


def _is_secret(name: str) -> bool:
    words = name.lower().split("_")
    return bool(_SECRET_WORDS & set(words)) or name.lower().endswith("api_key")


def _publishable_value(name: str, value: Any) -> str:
    if _is_secret(name):
        return "***"
    text = str(value)
    if "://" in text:
        # N4: never a URL query string or credentials in a URL.
        parts = urlsplit(text)
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        text = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return text


def command_line(ctx: typer.Context) -> list[str]:
    """The command as it ran, rebuilt from its parsed options, secrets replaced.

    Rebuilt rather than copied from `sys.argv`: the arguments are already
    parsed, so a secret is recognised by its parameter's name, not guessed
    from text. Options left at their default are omitted; with the same OPAAS
    version they produce the same run.
    """
    # The subcommand names, not `command_path`: under `python -m opaas.cli.main`
    # the root's name is the interpreter's invocation, not `opaas`.
    names: list[str] = []
    current: Any = ctx
    while current is not None and current.parent is not None:
        names.insert(0, current.info_name)
        current = current.parent
    words = ["opaas", *names]
    for param in ctx.command.params:
        value = ctx.params.get(param.name)
        if value is None or value == param.default:
            continue
        flags = [opt for opt in getattr(param, "opts", []) if opt.startswith("--")]
        values = value if isinstance(value, list | tuple) else [value]
        if param.param_type_name == "argument":
            words.extend(_publishable_value(param.name, item) for item in values)
            continue
        flag = flags[0] if flags else param.opts[0]
        if isinstance(value, bool):
            if value:
                words.append(flag)
            elif getattr(param, "secondary_opts", None):
                words.append(param.secondary_opts[0])
            continue
        for item in values:
            words.extend([flag, _publishable_value(param.name, item)])
    return words


@dataclass(slots=True)
class RecordedRun:
    """A command's run in the store, and what it decided about how it ended."""

    run: Run
    #: Set by the command when the result is weaker than a clean completion.
    status: str = "completed"
    kept: list[Path] = field(default_factory=list)
    #: Extra fields for `run.json`, set by the command (a draft's `sourceRun`).
    fields: dict[str, Any] = field(default_factory=dict)

    def keep(self, path: Path, name: str | None = None) -> None:
        """Records an output the command wrote outside the run directory."""
        self.run.keep(path, name)
        self.kept.append(path)

    @property
    def directory(self) -> Path:
        return self.run.directory


@contextmanager
def recorded_run(ctx: typer.Context, kind: str, *,
                 endpoint: str | None = None) -> Iterator[RecordedRun]:
    """Opens a run in the store for a command, and closes it however it ends.

    A clean end closes it `completed` (or whatever the command set); an error
    or a non-zero exit closes it `failed`, keeping what was written. An
    interrupt leaves it `running`: a reader then sees `interrupted`, because
    the process is gone (`docs/dashboard.md` §4).
    """
    try:
        run = open_run(
            kind,
            command=command_line(ctx),
            opaas_version=__version__,
            spec_version=OPAAS_SPEC_VERSION,
            endpoint=_publishable_value("endpoint", endpoint) if endpoint else None,
        )
    except (StoreError, OSError) as exc:
        err_console.print(f"[red]Cannot record the run:[/red] {exc}")
        raise typer.Exit(code=1) from None
    recorded = RecordedRun(run=run)
    try:
        yield recorded
    except KeyboardInterrupt:
        raise
    except typer.Exit as exc:
        code = getattr(exc, "exit_code", 0)
        run.close(recorded.status if code == 0 or recorded.status == "partial" else "failed",
                  **recorded.fields)
        raise
    except BaseException:
        run.close("failed", **recorded.fields)
        raise
    else:
        run.close(recorded.status, **recorded.fields)
    console.print(f"[dim]Recorded as run {run.run_id} in {run.directory.parent}[/dim]")
