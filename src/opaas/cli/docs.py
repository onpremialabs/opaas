"""Where the CLI points for more: the documentation in the OPAAS repository.

Help texts, error messages and the menu used to cite decisions by number —
"(D65)" — which tells a newcomer nothing and sends nobody anywhere. They now
link to the page and the section that answers the question. The links point
at `develop` on GitHub because that is where the documentation lives; the
package ships none of it.

Every anchor here is checked against the headings of the documents it points
into (`tests/test_cli_experience.py`), so renaming a heading breaks a test
rather than a link a user clicked on.
"""

from __future__ import annotations

__all__ = [
    "DECISIONS",
    "DOCS",
    "PANEL_AUDIT",
    "PANEL_RESULTS",
    "PANEL_SETUP",
    "PANEL_SIGNED",
    "REPOSITORY",
    "decision",
    "doc",
    "epilog",
]

REPOSITORY = "https://github.com/onpremialabs/opaas"
_BASE = f"{REPOSITORY}/blob/develop/"

#: Topic → (file in the repository, heading anchor or "").
DOCS: dict[str, tuple[str, str]] = {
    "readme": ("README.md", ""),
    "guide": ("docs/guide.md", ""),
    "model": ("docs/guide.md", "1--model-deployment"),
    "environment": ("docs/guide.md", "2--environment--configuration"),
    "agents": ("docs/guide.md", "3--agent-behaviour"),
    "auditability": ("docs/guide.md", "4--agent-auditability"),
    "system": ("docs/guide.md", "5--whole-system"),
    "monitoring": ("docs/guide.md", "6--continuous-monitoring"),
    "dashboard": ("docs/guide.md", "7--local-dashboard"),
    "run-store": ("docs/guide.md", "every-result-is-kept"),
    "signed": ("docs/guide.md", "signed-credentials"),
    "datasets": ("docs/guide.md", "datasets"),
    "reference": ("docs/reference.md", ""),
    "run-options": ("docs/reference.md", "run-options"),
    "commands": ("docs/reference.md", "command-reference"),
    "classifiers": ("docs/reference.md", "classifiers-faithfulness-and-toxicity"),
    "not-measured": ("docs/reference.md", "what-is-not-measured"),
    "methodology": ("docs/methodology.md", ""),
    "decisions": ("docs/decisions.md", ""),
    "ceremony": ("docs/ceremony.md", ""),
    "key-custody": ("docs/key-custody.md", ""),
}

#: The decisions the CLI's help refers to, by number → heading anchor in
#: `docs/decisions.md`.
DECISIONS: dict[int, str] = {
    36: "d36--the-sentinel-is-implemented-with-its-own-canaries-and-stays-local",
    40: "d40--the-report-records-everything-the-host-serves-not-only-what-was-evaluated",
    60: "d60--the-audited-subject-is-the-system-with-one-result-format-and-one-certificate",
    65: "d65--an-offline-root-on-two-tokens-delegates-to-an-issuing-key-issuances-stay-on-the-"
    "published-hash-chain",
    71: "d71--domain-scenario-packs-pinned-and-never-certifiable-alone-kind-aware-templates",
    76: "d76--every-result-is-recorded-in-a-run-store-written-once",
    79: "d79--opaas-ui-a-loopback-server-that-reads-the-store-and-launches-the-cli",
    81: "d81--the-certificate-of-a-run-is-the-final-audit-certificate-not-a-draft",
}


def doc(topic: str) -> str:
    """The URL of a documentation topic."""
    path, anchor = DOCS[topic]
    return _BASE + path + (f"#{anchor}" if anchor else "")


def decision(number: int) -> str:
    """The URL of a design decision: what was decided, why, and what it costs."""
    return f"{_BASE}docs/decisions.md#{DECISIONS[number]}"


def epilog(topic: str, *decisions: int) -> str:
    """The foot of a command's help: where it is documented, and why it works so."""
    lines = [f"Docs: {doc(topic)}"]
    lines.extend(f"Why it works this way: {decision(number)}" for number in decisions)
    # Typer's rich help joins single newlines into one paragraph.
    return "\n\n".join(lines)


# The help's panels, by task. A newcomer's commands come first; signing,
# which the audit flow does not need (D81), comes last and says so.
PANEL_AUDIT = "Audit"
PANEL_RESULTS = "Results"
PANEL_SETUP = "Data and setup"
PANEL_SIGNED = "Signed credentials (advanced)"
