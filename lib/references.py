"""Citation collection, renumbering, and the assembled report's reference list.

See sra6-spec.md §8.2 (the citation flow and the example reference line), §15.3
(assembly renumbers `[^bronze-id]` to `[^1..n]` and writes `references.md` plus
`citation_map.json`), and §20 (module contract).

Two invariants shape this module:

1. A reference entry always describes the artifact that was actually cited —
   the same id `citation_map.json` records and `validate` (§8.4) resolves. An
   aggregator's harvested origin and a computed artifact's upstream evidence
   are shown UNDER their entry rather than in place of it, so the rendered
   references and the citation map can never disagree about what supported a
   claim.
2. An id that resolves nowhere, or resolves to silver, raises. §8.2: "a
   citation that fails to resolve is a build defect" — `assemble` turns that
   into exit 1 rather than shipping a report with a dangling number.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lib.provenance import (
    DERIVED_SUBDIR, SourceMeta, StructuredMeta, read_source, read_structured,
    resolve_artifact,
)

# Same shape `validate` uses for citations, minus the footnote DEFINITION form
# (`[^id]:`), which is bookkeeping rather than a claim.
CITATION_RE = re.compile(r"\[\^([A-Za-z0-9][\w.\-]*)\](?!:)")

MACRO_TICKER = "_MACRO"

# Producers whose artifact is not itself fetched evidence: its citation expands
# to the bronze it was derived from (§15.3).
COMPUTED_PRODUCERS = frozenset({"compute"})

# Indent for the sub-lines under an entry (origin / derived-from expansions).
INDENT = "    "

# A malformed corpus must not hang assembly; expansion also stops on any id it
# has already rendered, so a derivation cycle terminates.
MAX_EXPANSION_DEPTH = 3


def collect_citations(md: str) -> list[str]:
    """Bronze ids cited in `md`, in order of first appearance, deduped (§15.3).

    Order of appearance is the numbering the reader sees, so this is also the
    order `build_references_md` renders.
    """
    return list(dict.fromkeys(CITATION_RE.findall(md)))


def renumber(md: str, mapping: dict[str, int]) -> str:
    """Rewrite `[^bronze-id]` to `[^N]` using `mapping` (§8.2).

    An id absent from `mapping` is left untouched: silently deleting it would
    hide the defect, and `validate`'s gold check (§8.2) fails on a leftover
    non-numeric citation, which is the outcome we want.
    """
    def sub(match: re.Match[str]) -> str:
        number = mapping.get(match.group(1))
        return match.group(0) if number is None else f"[^{number}]"

    return CITATION_RE.sub(sub, md)


def _macro_dir(ticker_dir: Path) -> Path:
    return ticker_dir.parent / MACRO_TICKER


def _resolve(ticker_dir: Path, artifact_id: str) -> tuple[Path, Path]:
    """Locate `artifact_id`, returning `(path, owning ticker dir)`.

    Ticker tree first, then the shared macro tree (§12) — the same order
    `validate._classify_citation` uses, so a citation that passes the gate
    always renders a reference.

    Raises `ValueError` for an id that resolves nowhere or resolves to silver.
    """
    for base in (ticker_dir, _macro_dir(ticker_dir)):
        if not base.is_dir():
            continue
        path = resolve_artifact(base, artifact_id)
        if path is None:
            continue
        if path.is_relative_to(base / DERIVED_SUBDIR):
            raise ValueError(
                f"citation [^{artifact_id}] resolves to a silver artifact "
                f"({path}): silver is never a citation target (§8.1)"
            )
        return path, base

    raise ValueError(
        f"citation [^{artifact_id}] resolves to no bronze artifact in "
        f"{ticker_dir.name} or {MACRO_TICKER} (§8.2)"
    )


def _date(stamp: str | None) -> str:
    """Date portion of an ISO timestamp (`2026-05-21T12:00:00+00:00` →
    `2026-05-21`). Already-bare dates pass through."""
    return (stamp or "").split("T", 1)[0]


def _describe_source(meta: SourceMeta) -> str:
    """`<title> — <source>, <url> — fetched <date>` (§8.2).

    A source with no URL — a Perplexity research output, say — drops the URL
    field entirely rather than rendering an empty one, giving §8.2's
    "Perplexity research, fetched <date>" form.
    """
    where = f"{meta.source}, {meta.url}" if meta.url else meta.source
    return f"{meta.title} — {where} — fetched {_date(meta.fetched_at)}"


def _describe_structured(meta: StructuredMeta) -> str:
    """Same line for a structured artifact. A computed artifact has no URL and
    no fetch timestamp, so it is dated by its `as_of` and its own entry says
    where the numbers came from — the upstream evidence follows underneath."""
    if meta.producer in COMPUTED_PRODUCERS:
        return f"{meta.title} — computed from bronze evidence — as of {meta.as_of}"
    where = f"{meta.source}, {meta.url}" if meta.url else meta.source
    fetched = _date(meta.fetched_at) or meta.as_of
    return f"{meta.title} — {where} — fetched {fetched}"


def _describe(path: Path) -> tuple[str, StructuredMeta | SourceMeta]:
    if path.suffix == ".json":
        meta, _ = read_structured(path)
        return _describe_structured(meta), meta
    source_meta, _ = read_source(path)
    return _describe_source(source_meta), source_meta


def _harvested_origins(ticker_dir: Path, meta: SourceMeta) -> list[str]:
    """Descriptions of bronze documents fetched from this aggregator's
    `cited_urls` (§5, §8.3).

    An aggregator reports on other documents; where `fetch-urls` has since
    harvested one of those URLs into bronze, the reader is pointed at that
    origin. A URL that was never harvested (fetch failed, or the researcher
    never cited it) contributes nothing — the aggregator stands on its own.
    """
    if not meta.cited_urls:
        return []

    by_url: dict[str, str] = {}
    for path in sorted((ticker_dir / "sources").glob("*.md")):
        other, _ = read_source(path)
        if other.id != meta.id and other.url:
            by_url.setdefault(other.url, _describe_source(other))

    return [by_url[url] for url in dict.fromkeys(meta.cited_urls) if url in by_url]


def _expand(ticker_dir: Path, base: Path, meta: SourceMeta | StructuredMeta,
            seen: set[str], depth: int) -> list[str]:
    """Sub-lines rendered under an entry: an aggregator's harvested origins, or
    a computed artifact's upstream bronze evidence."""
    if isinstance(meta, SourceMeta):
        return [f"{INDENT}origin: {text}"
                for text in _harvested_origins(ticker_dir, meta)]

    if meta.producer not in COMPUTED_PRODUCERS or depth >= MAX_EXPANSION_DEPTH:
        return []

    lines: list[str] = []
    for upstream_id in meta.derived_from:
        if upstream_id in seen:
            continue
        seen.add(upstream_id)
        try:
            path, owner = _resolve(base, upstream_id)
        except ValueError:
            # A derivation stamp that no longer resolves is `validate`'s
            # finding to report (§8.4 check 5), not a reason to abort the
            # reference list mid-render.
            continue
        text, upstream_meta = _describe(path)
        lines.append(f"{INDENT}derived from: {text}")
        lines += [
            f"{INDENT}{line}"
            for line in _expand(owner, owner, upstream_meta, seen, depth + 1)
        ]
    return lines


def build_references_md(ticker_dir: Path, ids: list[str]) -> str:
    """The report's reference section: one numbered entry per id, in the order
    supplied (§8.2, §15.3).

    Expansions (origins, upstream evidence) do not consume reference numbers —
    they belong to the entry above them, and the numbering has to stay aligned
    with `citation_map.json`.
    """
    lines = ["## References", ""]
    for number, artifact_id in enumerate(ids, start=1):
        path, owner = _resolve(ticker_dir, artifact_id)
        text, meta = _describe(path)
        lines.append(f"[{number}] {text}")
        lines += _expand(owner, owner, meta, {artifact_id}, 0)
    return "\n".join(lines) + "\n"


def write_citation_map(run_dir: Path, mapping: dict[int, str]) -> Path:
    """Write `reports/<run>/citation_map.json` and return its path (§8.2).

    Keys are serialized as strings (`{"1": "2026-05-21_sec_10q"}`) — the shape
    §8.2 shows and the shape `validate` reads back.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "citation_map.json"
    payload = {str(number): artifact_id for number, artifact_id in sorted(mapping.items())}
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path
