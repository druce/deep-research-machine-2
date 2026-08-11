"""Provenance foundation: kind sets, metadata dataclasses, and source-id allocation.

This is the safety-critical module that keeps model-generated text out of evidence
directories and makes every citation resolvable. See sra6-spec.md §5 (sources/),
§6 (structured/), and §20 (lib/provenance.py module contract).

This module currently provides only the foundation: the two disjoint kind sets, the
two metadata dataclasses, and `make_source_id`. Writers and resolvers (`write_source`,
`resolve_source`, `read_source`, `write_structured`, `write_derived`, `write_answer`)
are implemented in later tasks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Valid under sources/ (§5).
BRONZE_KINDS = frozenset({
    "sec_filing", "wikipedia", "news", "perplexity_research",
    "transcript", "web_page", "press_release", "analyst_note", "other",
})

# Valid for researcher answers under derived/answers/ (§5).
MODEL_KINDS = frozenset({"research_answer"})

DERIVED_SUBDIR = "derived"
SOURCE_COMPUTED = "computed"


@dataclass
class SourceMeta:
    """Frontmatter fields for a bronze document under sources/ (§5, §20)."""

    id: str
    ticker: str
    kind: str
    source: str
    url: str
    fetched_at: str
    as_of: str
    title: str
    fetch_tool: str
    fetch_cmd: str
    request: dict | None = None
    supersedes: str | None = None
    cited_urls: list[str] = field(default_factory=list)


@dataclass
class StructuredMeta:
    """`_meta` fields for a bronze/silver JSON artifact under structured/ or derived/ (§6, §20)."""

    id: str
    ticker: str
    producer: str
    title: str
    source: str
    as_of: str
    provider_tool: str | None = None
    fetch_cmd: str | None = None
    url: str | None = None
    request: dict | None = None
    fetched_at: str | None = None
    computed_at: str | None = None
    generated_at: str | None = None
    period: str | None = None
    currency: str | None = None
    adjusted: bool | None = None
    derived_from: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    """Lowercase, collapse runs of non-alphanumerics to a single hyphen, strip ends."""
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def _archived_id(filename: str) -> str:
    """Recover a source id from an archived filename by stripping the trailing
    `_<YYYY-MM-DD>.md` suffix that records when it was superseded (§5). This is safe
    even when the id itself ends in a `_<n>` suffix, since only a trailing date-shaped
    suffix is stripped."""
    return re.sub(r"_\d{4}-\d{2}-\d{2}\.md$", "", filename)


def make_source_id(kind: str, on: date, topic: str | None = None, *, ticker_dir: Path) -> str:
    """Allocate a source id, picking the smallest free `_<n>` suffix against
    sources/ and sources/archive/ together so an id is never reused after
    archiving (§5, §20).

    NOTE: spec §20 writes this as `make_source_id(kind, on, topic=None)`, but §5/§20
    also require scanning `sources/` and `sources/archive/` for collisions, which is
    not computable without the ticker directory. `ticker_dir` is added as a required
    keyword-only parameter to resolve that gap.
    """
    base = f"{on.isoformat()}_{kind}" + (f"_{_slug(topic)}" if topic else "")
    taken = {p.stem for p in (ticker_dir / "sources").glob("*.md")}
    taken |= {_archived_id(p.name) for p in (ticker_dir / "sources" / "archive").glob("*.md")}
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"
