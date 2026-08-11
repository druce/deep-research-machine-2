"""Provenance foundation: kind sets, metadata dataclasses, and source-id allocation.

This is the safety-critical module that keeps model-generated text out of evidence
directories and makes every citation resolvable. See sra6-spec.md §5 (sources/),
§6 (structured/), and §20 (lib/provenance.py module contract).

This module provides the two disjoint kind sets, the two metadata dataclasses,
`make_source_id`, and the bronze source I/O: `write_source`, `resolve_source`, and
`read_source`. `write_structured`, `write_derived`, and `write_answer` are
implemented in later tasks.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

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
    request: dict[str, object] | None = None
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
    request: dict[str, object] | None = None
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
    """Recover a source id from an archived filename by stripping the extension and,
    if present, the trailing `_<YYYY-MM-DD>` suffix that records when it was
    superseded (§5). This is safe even when the id itself ends in a `_<n>` suffix,
    since only a trailing date-shaped suffix is stripped. Total over any filename:
    a name that does not carry the date suffix (non-conforming or hand-placed) still
    has its extension stripped, so it cannot silently fail to match a base id and
    fall out of the `taken` set (that failure mode would let two documents answer to
    one citation key, which §5 forbids)."""
    return re.sub(r"_\d{4}-\d{2}-\d{2}$", "", Path(filename).stem)


def _reject_path_traversal(value: str, field_name: str) -> None:
    """Guard an id/supersedes value that will be interpolated into a path under
    `sources/` or `sources/archive/` (§8.4 requires path containment
    structurally). `make_source_id` only ever produces bare filename
    components, so this never fires on that path; it exists for callers that
    construct `SourceMeta` by hand.

    Rejects an empty value, any path separator, and any `..` segment.
    """
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ValueError(
            f"{field_name} {value!r} must be a bare filename component "
            f"(no path separators or '..')"
        )


def make_source_id(kind: str, on: date, topic: str | None = None, *, ticker_dir: Path) -> str:
    """Allocate a source id, picking the smallest free `_<n>` suffix against
    sources/ and sources/archive/ together so an id is never reused after
    archiving (§5, §20).

    NOTE: spec §20 writes this as `make_source_id(kind, on, topic=None)`, but §5/§20
    also require scanning `sources/` and `sources/archive/` for collisions, which is
    not computable without the ticker directory. `ticker_dir` is added as a required
    keyword-only parameter to resolve that gap.
    """
    sources_dir = ticker_dir / "sources"
    if not sources_dir.is_dir():
        raise FileNotFoundError(f"no sources/ directory under {ticker_dir}")
    slug = _slug(topic) if topic else ""
    base = f"{on.isoformat()}_{kind}" + (f"_{slug}" if slug else "")
    taken = {p.stem for p in sources_dir.glob("*.md")}
    taken |= {_archived_id(p.name) for p in (sources_dir / "archive").glob("*.md")}
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def write_source(
    ticker_dir: Path, meta: SourceMeta, body: str, *, today: date | None = None
) -> Path:
    """Write a bronze document to `sources/<id>.md` (§5, §20).

    Raises `ValueError` if `meta.kind` is not in `BRONZE_KINDS` (this is where
    `MODEL_KINDS` are rejected — model output never lands under `sources/`),
    or if `meta.id`/`meta.supersedes` is not a bare filename component (§8.4).
    Raises `FileExistsError` if the id already exists anywhere — currently in
    `sources/` or already archived under `sources/archive/`: sources are
    immutable, so a refresh always writes a new id rather than overwriting,
    and an id is never reused after archiving (§5's "ids are unique across
    both directories").

    If `meta.supersedes` names a source currently in `sources/`, that file is
    moved (not rewritten) to `sources/archive/<old-id>_<today>.md` before the
    new file is written, where `today` defaults to `date.today()` and is
    injectable for deterministic tests. The move preserves the old file's
    bytes exactly, frontmatter included (§5's "byte-identical" requirement).
    Raises `FileExistsError` if that archive destination is already occupied
    — the archive is the only copy of superseded evidence, so this never
    overwrites silently.

    If `meta.supersedes` names a source that is no longer in `sources/`
    (already archived by an earlier attempt, or never existed), archiving is
    a silent no-op and the write proceeds — this makes a half-completed
    retry safe to re-run (§7.1).

    The file itself is written atomically (temp file in `sources/`, then
    `os.replace`), so a crash mid-write cannot leave a truncated file
    occupying the id forever.
    """
    if meta.kind not in BRONZE_KINDS:
        raise ValueError(
            f"write_source rejects kind {meta.kind!r}: not in BRONZE_KINDS "
            f"(did you mean write_answer for a MODEL_KINDS artifact?)"
        )
    _reject_path_traversal(meta.id, "meta.id")
    if meta.supersedes:
        _reject_path_traversal(meta.supersedes, "meta.supersedes")

    sources_dir = ticker_dir / "sources"
    target = sources_dir / f"{meta.id}.md"
    if target.exists() or resolve_source(ticker_dir, meta.id) is not None:
        raise FileExistsError(
            f"source id already exists (sources are immutable, ids are unique "
            f"across sources/ and sources/archive/): {meta.id}"
        )

    if meta.supersedes:
        old_path = sources_dir / f"{meta.supersedes}.md"
        if old_path.exists():
            archive_dir = sources_dir / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = (today or date.today()).isoformat()
            archived_path = archive_dir / f"{meta.supersedes}_{stamp}.md"
            if archived_path.exists():
                raise FileExistsError(
                    f"archive destination already exists, refusing to overwrite "
                    f"superseded evidence: {archived_path}"
                )
            shutil.move(str(old_path), str(archived_path))
        # else: already archived by an earlier attempt, or never existed —
        # idempotent no-op (§7.1); the write below still proceeds.

    metadata: dict[str, object] = {
        "id": meta.id,
        "ticker": meta.ticker,
        "kind": meta.kind,
        "source": meta.source,
        "url": meta.url,
        "fetched_at": meta.fetched_at,
        "as_of": meta.as_of,
        "title": meta.title,
        "fetch_tool": meta.fetch_tool,
        "fetch_cmd": meta.fetch_cmd,
    }
    if meta.request:
        metadata["request"] = meta.request
    if meta.supersedes:
        metadata["supersedes"] = meta.supersedes
    if meta.cited_urls:
        metadata["cited_urls"] = meta.cited_urls

    post = frontmatter.Post(body, **metadata)
    text = frontmatter.dumps(post, sort_keys=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(dir=sources_dir, prefix=f".{meta.id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def resolve_source(ticker_dir: Path, source_id: str) -> Path | None:
    """Resolve a source id to its path, current or archived (§5, §20).

    Looks in `sources/` first (exact filename match); if not found there,
    scans `sources/archive/` for a file whose id — recovered via
    `_archived_id`, the same helper `make_source_id` uses to know which ids
    are taken — equals `source_id`. Every id-to-path lookup (`show`,
    citation resolution, reference building) goes through this function, so
    no caller has to know whether a source is current or superseded.

    Returns `None` if `source_id` resolves nowhere.
    """
    sources_dir = ticker_dir / "sources"
    current = sources_dir / f"{source_id}.md"
    if current.exists():
        return current

    archive_dir = sources_dir / "archive"
    if archive_dir.is_dir():
        for candidate in archive_dir.glob("*.md"):
            if _archived_id(candidate.name) == source_id:
                return candidate

    return None


def read_source(path: Path) -> tuple[SourceMeta, str]:
    """Read a bronze document, round-tripping anything `write_source` wrote.

    Optional fields omitted from frontmatter (`request`, `supersedes`,
    `cited_urls`) are filled with `SourceMeta`'s own defaults (`None`, `None`,
    `[]`) rather than surfacing as missing-key errors.
    """
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    md = post.metadata
    meta = SourceMeta(
        id=md["id"],
        ticker=md["ticker"],
        kind=md["kind"],
        source=md["source"],
        url=md["url"],
        fetched_at=md["fetched_at"],
        as_of=md["as_of"],
        title=md["title"],
        fetch_tool=md["fetch_tool"],
        fetch_cmd=md["fetch_cmd"],
        request=md.get("request"),
        supersedes=md.get("supersedes"),
        cited_urls=list(md.get("cited_urls") or []),
    )
    return meta, post.content
