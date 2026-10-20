"""Every link to the repository names onpremialabs/opaas, never an earlier name.

The repository was luxinopanyvino/opaas-bench before it moved. GitHub
redirects the old URLs, but only until someone takes the old name: from that
day every install command, citation and package link that still used it
points at somebody else's code. So the old names are kept out of the files
users copy from, and the package metadata names the repository itself.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "https://github.com/onpremialabs/opaas"

#: Earlier homes of the repository, written so this file does not match itself.
OLD_URLS = re.compile(
    r"github\.com/(" + "luxinopanyvino" + r"/|[\w.-]+/" + "opas-bench" + r"\b|[\w.-]+/"
    + "opaas-bench" + r"\b)",
    re.IGNORECASE,
)

#: What users read, copy or install from: the docs, the package, its metadata, CI.
SCANNED = ("README.md", "CHANGELOG.md", "CITATION.cff", "pyproject.toml", "CLAUDE.md",
           "docs", "src", ".github", "scripts", "web")
SUFFIXES = {".md", ".py", ".toml", ".cff", ".yml", ".yaml", ".html", ".js", ".css", ".json",
            ".txt"}


def _files():
    for name in SCANNED:
        path = ROOT / name
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (p for p in path.rglob("*")
                        if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts)


def test_no_file_links_to_an_earlier_name_of_the_repository():
    found = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in _files()
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace")
                                      .splitlines(), start=1)
        if OLD_URLS.search(line)
    ]
    assert not found, "Links to an earlier repository name:\n" + "\n".join(found)


def test_the_package_and_its_citation_name_the_repository():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert f'Repository = "{CANONICAL}"' in pyproject
    assert f'repository-code: "{CANONICAL}"' in citation
