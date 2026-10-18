"""EsBBQ importer to the OPAAS format: social bias in Spanish (block D, D69).

EsBBQ (Ruiz-Fernández et al., 2025; BSC and HiTZ) adapts BBQ to Spanish and to
the social context of Spain: the same design of two people, one ambiguous and
one disambiguated context, a negative and a non-negative question, over ten
categories (BBQ's nine with sexual orientation widened to LGBTQIA, plus Spanish
region). It is read from the authors' repository at a fixed commit, where it is
distributed under Apache-2.0; the Hugging Face copy is CC-BY-4.0. Both allow
publishing results.

**The stereotyped answer is in each record.** BBQ needs a separate metadata
file to say which answer the stereotype targets; EsBBQ carries it inline: the
answer whose social group (the last element of `answer_info`) appears in
`stereotyped_groups`. A record where that is not exactly one of the two people
is dropped whole (D15). In the pinned files every record has exactly one.

**Two changes to the items, both declared.** In the source the third option is
always the literal English word `unknown`, and always third. The authors
replace it at evaluation time with a list of Spanish wordings scored by
log-likelihood; OPAAS asks for a letter instead, so:

- the option reads `No se puede determinar`, one fixed Spanish wording, the
  counterpart of BBQ's "Can't be determined";
- the three options are put in an order derived from the item's id (a hash, so
  it does not depend on the seed or on which items are selected). Left as
  shipped, a model that leans towards the letter C would look unbiased.

Figures are therefore not comparable with the paper's, which scores
likelihoods over eleven options. Same questions, different measurement.

**A designed subset, as for BBQ.** 100 ambiguous and 100 disambiguated
questions per category, each half negative and half non-negative, drawn with a
fixed seed: 2,000 items over ten categories.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path
from typing import Any

#: The ten categories, as EsBBQ names its files (`data_es/<Category>.full.jsonl`).
CATEGORIES: tuple[str, ...] = (
    "Age",
    "DisabilityStatus",
    "Gender",
    "LGBTQIA",
    "Nationality",
    "PhysicalAppearance",
    "RaceEthnicity",
    "Religion",
    "SES",
    "SpanishRegion",
)

#: Items per (category, condition) cell, split evenly between polarities. The
#: smallest category (Nationality, 504 records) still fills every cell.
PER_CELL = 100

MAX_TOKENS = 64

#: The wording that replaces the source's literal `unknown` (see the docstring).
UNKNOWN_ES = "No se puede determinar"

_LETTERS = "ABC"
_ORDERS: tuple[tuple[int, int, int], ...] = tuple(permutations(range(3)))


class ImportError_(ValueError):
    """The source files are not EsBBQ."""


@dataclass
class ImportStats:
    total: int = 0
    converted: int = 0
    skipped: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    skipped_by_reason: dict[str, int] = field(default_factory=dict)


def file_name(category: str) -> str:
    return f"{category}.full.jsonl"


def read_source(path: Path) -> list[dict[str, Any]]:
    """`path` is any one of the category files; the other nine sit beside it."""
    if not path.exists():
        raise ImportError_(f"The source file does not exist: {path}")
    rows: list[dict[str, Any]] = []
    for category in CATEGORIES:
        file = path.parent / file_name(category)
        if not file.is_file():
            raise ImportError_(
                f"{file} is missing. EsBBQ needs its ten category files side by side; "
                "fetch the source again with: opaas fetch esbbq"
            )
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ImportError_(f"{file}:{number} is not valid JSON: {exc}") from None
    if not rows:
        raise ImportError_(f"No EsBBQ questions found next to {path}")
    return rows


def convert(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], ImportStats]:
    items: list[dict[str, Any]] = []
    stats = ImportStats(total=len(rows))
    for row in rows:
        item = _convert_row(row)
        if isinstance(item, str):
            stats.skipped += 1
            stats.skipped_by_reason[item] = stats.skipped_by_reason.get(item, 0) + 1
            continue
        items.append(item)
        stats.converted += 1
        stats.by_category[item["category"]] = stats.by_category.get(item["category"], 0) + 1
    return items, stats


def _group(info: Any) -> str | None:
    """The social group of one answer: the last element, after its last comma."""
    if not isinstance(info, list) or not info:
        return None
    return str(info[-1]).split(",")[-1].strip()


def _convert_row(row: dict[str, Any]) -> dict[str, Any] | str:
    """A whole item, or the reason there is none (D15)."""
    info = row.get("answer_info") or {}
    groups = [_group(info.get(f"ans{i}")) for i in range(3)]
    stereotyped = row.get("stereotyped_groups")
    if not isinstance(stereotyped, list):
        return "stereotyped groups not recorded"
    targets = [i for i in (0, 1) if groups[i] in stereotyped]
    if len(targets) != 1:
        return "stereotyped answer not identifiable from the metadata"
    people = [str(row.get(f"ans{i}") or "").strip() for i in (0, 1)]
    label = row.get("label")
    if (
        not all(people)
        or groups[2] != "unknown"
        or label not in (0, 1, 2)
        or row.get("question_polarity") not in ("neg", "nonneg")
        or row.get("context_condition") not in ("ambig", "disambig")
        or not row.get("context")
        or not row.get("question")
        or row.get("category") is None
        or row.get("instance_id") is None
    ):
        return "malformed question record"

    item_id = f"esbbq-{row['category']}-{row['instance_id']}"
    # Source position -> displayed position, fixed by the id alone.
    order = _ORDERS[int(hashlib.sha256(item_id.encode()).hexdigest(), 16) % len(_ORDERS)]
    source_choices = [*people, UNKNOWN_ES]
    choices = [source_choices[i] for i in order]
    letter = {source: _LETTERS[shown] for shown, source in enumerate(order)}
    return {
        "id": item_id,
        "category": str(row["category"]),
        "context": str(row["context"]).strip(),
        "prompt": str(row["question"]).strip(),
        "choices": choices,
        "answer": letter[label],
        "unknown": letter[2],
        "target": letter[targets[0]],
        "polarity": row["question_polarity"],
        "condition": row["context_condition"],
        "max_tokens": MAX_TOKENS,
    }


def sample(items: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], str]:
    """PER_CELL items per (category, condition), half of each polarity."""
    rng = random.Random(seed)
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in sorted(items, key=lambda i: i["id"]):
        cells.setdefault((item["category"], item["condition"], item["polarity"]), []).append(item)
    selected: list[dict[str, Any]] = []
    for key in sorted(cells):
        selected += rng.sample(cells[key], min(PER_CELL // 2, len(cells[key])))
    return selected, (
        f"{PER_CELL} ambiguous + {PER_CELL} disambiguated per category, "
        "half negative and half non-negative; options reordered by item id"
    )


TASK = {
    "file": "bias_es.jsonl",
    "name": "esbbq",
    "category": "bias",
    "type": "bias_mc",
    "language": "es",
    "description": "EsBBQ — does the model pick a stereotype, in Spanish, when the context "
    "does not say?",
}
MANIFEST = {
    "license": "Apache-2.0 (Ruiz-Fernández et al., 2025; the Hugging Face copy is CC-BY-4.0)",
    "source": "https://github.com/langtech-bsc/SpainBBQ",
}
