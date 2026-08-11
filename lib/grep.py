"""Deterministic term search over bronze documents (spec §9, §20).

Step two of the researcher's progressive-disclosure path: read
`sources/00_manifest.md`, `grep` to find the relevant documents, `show` to read
one whole. There is no vector store and no similarity search anywhere in the
pipeline (§9.1) — this module is the entire retrieval mechanism, which is why
its ranking has to be reproducible rather than merely reasonable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from lib.manifest import MANIFEST_NAME
# Imported rather than reimplemented so the `<id>_<superseded-date>.md` archive
# naming convention has exactly one definition (§5); duplicating the regex here
# is how the two would eventually disagree about what an archived id is.
from lib.provenance import _archived_id


@dataclass
class Hit:
    """One matching document. One Hit per document, never per line: matches on
    several lines collapse into a single Hit with merged `matched_terms`."""

    source_id: str
    kind: str
    as_of: str
    url: str
    title: str
    excerpt: str
    matched_terms: list[str] = field(default_factory=list)


def _compile_terms(pattern: str) -> list[tuple[str, re.Pattern[str]]]:
    """Split the pattern on whitespace and compile each term as a
    case-insensitive regex, keeping the original text so `matched_terms`
    reports what the caller typed.

    An invalid term raises `ValueError` naming it, rather than being silently
    dropped — a researcher whose regex is malformed must not read the empty
    result as "no such evidence exists".
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for term in pattern.split():
        try:
            compiled.append((term, re.compile(term, re.IGNORECASE)))
        except re.error as exc:
            raise ValueError(f"invalid regex term {term!r}: {exc}") from exc
    return compiled


def _candidate_paths(ticker_dir: Path, include_archived: bool) -> list[Path]:
    """Current sources, plus `sources/archive/` when asked (§9).

    `00_manifest.md` is always skipped: it lives in `sources/` but is a
    generated catalog, and a hit inside it would hand back a table row rather
    than evidence.
    """
    sources_dir = ticker_dir / "sources"
    if not sources_dir.is_dir():
        return []
    paths = [p for p in sources_dir.glob("*.md") if p.name != MANIFEST_NAME]
    if include_archived:
        paths += list((sources_dir / "archive").glob("*.md"))
    return paths


class _Desc:
    """Sort helper: wraps a string so it orders DESCENDING inside an otherwise
    ascending tuple key. Lets the three ranking keys — one descending between
    two ascending — live in a single `sort` call, rather than a chain of
    stable passes whose order is easy to get backwards."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __lt__(self, other: "_Desc") -> bool:
        return self.value > other.value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Desc) and self.value == other.value


def _excerpt(lines: list[str], line_no: int, context: int) -> str:
    """The matching line plus `context` lines on each side, clamped to the
    document."""
    start = max(0, line_no - context)
    end = min(len(lines), line_no + context + 1)
    return "\n".join(lines[start:end])


def grep(
    ticker_dir: Path,
    pattern: str,
    kinds: list[str] | None = None,
    context: int = 2,
    top_k: int | None = None,
    include_archived: bool = False,
) -> list[Hit]:
    """Search bronze document bodies and return ranked hits (§9).

    `pattern` is whitespace-separated terms, each a case-insensitive regex
    matched per line. A document hits if ANY term matches; how many distinct
    terms it matches is what ranks it.

    Only the BODY is searched, never frontmatter — otherwise a query would
    match the provider name or the recorded fetch command and return metadata
    dressed as evidence.

    Ranking, deterministic per §9:

    1. count of distinct matched terms, descending,
    2. `as_of`, descending,
    3. `source_id`, ascending.

    §9 names the first two. The third is added because two documents can tie
    on both, and without it the order would fall through to filesystem
    enumeration — which would make identical corpora rank differently on
    different machines and break `eval-retrieval` (§9.2) as a regression test.

    `excerpt` is built from the FIRST matching line, with `context` lines of
    surrounding text; `matched_terms` lists the terms that matched anywhere in
    the document, deduped and in pattern order.
    """
    terms = _compile_terms(pattern)
    if not terms:
        return []
    wanted_kinds = set(kinds) if kinds else None

    hits: list[Hit] = []
    for path in _candidate_paths(ticker_dir, include_archived):
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        kind = str(post.metadata.get("kind") or "")
        if wanted_kinds is not None and kind not in wanted_kinds:
            continue

        lines = post.content.splitlines()
        matched: list[str] = []
        first_match: int | None = None
        for text, regex in terms:
            for line_no, line in enumerate(lines):
                if regex.search(line):
                    matched.append(text)
                    if first_match is None or line_no < first_match:
                        first_match = line_no
                    break  # distinct terms rank, not repetitions

        if first_match is None:
            continue

        hits.append(Hit(
            # An archived file is named `<id>_<superseded-date>.md`, so the id
            # comes from frontmatter (falling back to the same helper
            # `resolve_source` uses) — a citation must never be handed the
            # on-disk stem.
            source_id=str(post.metadata.get("id") or _archived_id(path.name)),
            kind=kind,
            as_of=str(post.metadata.get("as_of") or ""),
            url=str(post.metadata.get("url") or ""),
            title=str(post.metadata.get("title") or ""),
            excerpt=_excerpt(lines, first_match, context),
            matched_terms=matched,
        ))

    hits.sort(key=lambda h: (-len(h.matched_terms), _Desc(h.as_of), h.source_id))
    return hits[:top_k] if top_k is not None else hits
