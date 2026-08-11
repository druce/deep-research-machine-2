"""Provenance foundation: kind sets, metadata dataclasses, and source-id allocation.

This is the safety-critical module that keeps model-generated text out of evidence
directories and makes every citation resolvable. See sra6-spec.md §5 (sources/),
§6 (structured/), and §20 (lib/provenance.py module contract).

This module provides the two disjoint kind sets, the two metadata dataclasses,
`make_source_id`, the bronze source I/O (`write_source`, `resolve_source`,
`read_source`), the silver research-answer writer (`write_answer`), the
producer-shape checks (`check_fetch_shape`, `check_compute_shape`,
`check_model_shape`, `check_request_credentials`), and the structured/derived
JSON I/O: `write_structured` (bronze, `fetch`/`compute` only), `write_derived`
(silver, `fetch`/`compute`/`model`), and `read_structured`.
"""

from __future__ import annotations

import json
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

# Parameter names that must never appear inside meta.request (§5): a credential
# is omitted entirely, never blanked or masked, so even an empty/placeholder
# value under one of these keys is rejected.
CREDENTIAL_PARAM_NAMES = frozenset({"apikey", "api_key", "token", "access_token"})


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


def _write_source_md(target_dir: Path, meta: SourceMeta, body: str) -> Path:
    """Serialize a `SourceMeta` + body to frontmatter markdown and write it
    atomically to `target_dir / f"{meta.id}.md"` (temp file in `target_dir`,
    then `os.replace` — the same pattern `_write_structured_json` uses for
    JSON). Shared by `write_source` and `write_answer`, which differ only in
    which kind set they check, which directory they target, and whether
    archiving applies — none of which touches serialization or atomicity, so
    both are kept here in one place rather than duplicated.

    Optional fields (`request`, `supersedes`, `cited_urls`) are omitted from
    frontmatter when falsy, matching §5's example of a bare document.
    """
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

    target = target_dir / f"{meta.id}.md"
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=f".{meta.id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


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

    return _write_source_md(sources_dir, meta, body)


def write_answer(ticker_dir: Path, meta: SourceMeta, body: str) -> Path:
    """Write a silver research answer to `derived/answers/<id>.md` (§1.2, §5
    MODEL_KINDS, §8.1, §20).

    This is the sanctioned home for model-synthesized research answers — the
    historical defect §1.2 documents is a `research_answer` landing in
    `sources/`, getting indexed and cited exactly like a filing, so a report
    citation could terminate at model-generated text rather than evidence.
    Routing every `MODEL_KINDS` write through here, and only through here,
    keeps that from happening again.

    Raises `ValueError` if `meta.kind` is not in `MODEL_KINDS` (this is where
    `BRONZE_KINDS` are rejected — a bronze kind never lands under
    `derived/answers/`; use `write_source` instead), or if `meta.id` is not a
    bare filename component (§8.4).

    Raises `FileExistsError` on overwrite. Unlike `write_source`, there is no
    archive-on-supersede path: an answer is an audit record of what a
    researcher produced in a given round, and answers are never superseded,
    only ever added to.

    Uses the same frontmatter serialization and atomic-write helper as
    `write_source` (`_write_source_md`), so `read_source` reads an answer
    back unchanged, `cited_urls` included.
    """
    if meta.kind not in MODEL_KINDS:
        raise ValueError(
            f"write_answer rejects kind {meta.kind!r}: not in MODEL_KINDS "
            f"(did you mean write_source for a BRONZE_KINDS artifact?)"
        )
    _reject_path_traversal(meta.id, "meta.id")

    answers_dir = ticker_dir / DERIVED_SUBDIR / "answers"
    target = answers_dir / f"{meta.id}.md"
    if target.exists():
        raise FileExistsError(
            f"answer id already exists (answers are audit records, never "
            f"overwritten): {meta.id}"
        )

    answers_dir.mkdir(parents=True, exist_ok=True)
    return _write_source_md(answers_dir, meta, body)


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


# --- producer-shape checks (§6.2) -----------------------------------------
#
# Each function takes a `StructuredMeta` and returns a list of human-readable
# problem strings (empty means valid). They are reused unchanged by
# `write_structured`/`write_derived` and, in Task 4.1, by `sra.py validate`
# (§8.4 check 1) — a validator reads a meta off disk via `read_structured`
# and calls the same function, so the two code paths cannot disagree about
# what a valid artifact is.

def check_fetch_shape(meta: StructuredMeta) -> list[str]:
    """§6.2 `fetch`: id, ticker, title, source, url, fetched_at, as_of,
    provider_tool, fetch_cmd all required and non-empty."""
    problems: list[str] = []
    for name in (
        "id", "ticker", "title", "source", "url", "fetched_at", "as_of",
        "provider_tool", "fetch_cmd",
    ):
        if not getattr(meta, name, None):
            problems.append(f"fetch producer requires non-empty {name!r}")
    return problems


def check_compute_shape(meta: StructuredMeta) -> list[str]:
    """§6.2 `compute`: id, ticker, title, source, derived_from (non-empty),
    computed_at, as_of, provider_tool, fetch_cmd required; url must be ABSENT."""
    problems: list[str] = []
    for name in (
        "id", "ticker", "title", "source", "computed_at", "as_of",
        "provider_tool", "fetch_cmd",
    ):
        if not getattr(meta, name, None):
            problems.append(f"compute producer requires non-empty {name!r}")
    if not meta.derived_from:
        problems.append("compute producer requires non-empty 'derived_from'")
    if meta.url:
        problems.append(f"compute producer forbids 'url' (got {meta.url!r})")
    return problems


def check_model_shape(meta: StructuredMeta) -> list[str]:
    """§6.2 `model`: id, ticker, title, source, derived_from (non-empty),
    generated_at, as_of required; url and fetch_cmd must be ABSENT."""
    problems: list[str] = []
    for name in ("id", "ticker", "title", "source", "generated_at", "as_of"):
        if not getattr(meta, name, None):
            problems.append(f"model producer requires non-empty {name!r}")
    if not meta.derived_from:
        problems.append("model producer requires non-empty 'derived_from'")
    if meta.url:
        problems.append(f"model producer forbids 'url' (got {meta.url!r})")
    if meta.fetch_cmd:
        problems.append(f"model producer forbids 'fetch_cmd' (got {meta.fetch_cmd!r})")
    return problems


_SHAPE_CHECKERS = {
    "fetch": check_fetch_shape,
    "compute": check_compute_shape,
    "model": check_model_shape,
}


def _is_credential_pair(value: object) -> bool:
    """Is `value` itself a `(name, value)` pair whose first element is a
    credential parameter name? Restricted to ordered sequences (`list`/
    `tuple`, exactly length 2) — a `set`/`frozenset` has no "first element"
    to check, so a bare single-element credential-name set (nothing to leak;
    e.g. `{"apikey"}`) and any other set/frozenset are correctly left alone
    rather than guessed at. A 3+-element sequence starting with a credential
    name is likewise out of scope: it is not the `(name, value)` shape a
    real client produces."""
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], str)
        and value[0].lower() in CREDENTIAL_PARAM_NAMES
    )


def _find_credential_keys(value: object) -> list[str]:
    """Walk a nested structure and collect every credential parameter name
    found at any depth, in either of the two shapes a real HTTP client
    produces:

    - a dict key whose lowercased form is in `CREDENTIAL_PARAM_NAMES`
      (e.g. `request.params.token`, or nested inside a list/tuple of dicts
      under `request.body`), or
    - a `(name, value)` pair (`_is_credential_pair`) — because both `httpx`
      and `requests` accept `params`/`body` as a sequence of pairs (the
      "repeated query parameter" form), and `json.dump` serializes a tuple
      as a JSON array indistinguishable from a list, so a credential
      recorded that way must be caught the same as a dict key.

    The pair-check is applied to every sequence this function VISITS, not
    only to items encountered while already iterating a recursed container —
    so a flat pair reached as a dict's direct value (`{"params": ["apikey",
    "SECRET"]}`), as a list element, or at the top level are all treated
    identically. An earlier version checked the pair-shape only from inside
    the `elif` branch's iteration, which caught a pair nested inside another
    list but missed the flat case: the walker never asked "is this container
    itself a pair?" before recursing into it, only "is this item, found
    while iterating, a pair?".

    Recurses into `dict`, `list`, `tuple`, `set`, and `frozenset` alike — a
    tuple is not just a list-lookalike here, it is the literal shape
    `params=list(client_params)` or `params=tuple(...)` produces.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, val in value.items():
            if isinstance(key, str) and key.lower() in CREDENTIAL_PARAM_NAMES:
                found.append(key)
            found.extend(_find_credential_keys(val))
    elif isinstance(value, (list, tuple, set, frozenset)):
        if _is_credential_pair(value):
            found.append(value[0])
        for item in value:
            found.extend(_find_credential_keys(item))
    return found


def check_request_credentials(request: dict[str, object] | None) -> list[str]:
    """Reject a credential parameter anywhere in `request`, nested arbitrarily
    through dicts and lists (§5, §8.4 check 6). A credential must be omitted
    entirely, not blanked or masked, so this fires even on an empty or
    placeholder value."""
    if not request:
        return []
    return [
        f"request contains forbidden credential parameter {name!r} "
        f"(§5: omit entirely, never blank or mask)"
        for name in _find_credential_keys(request)
    ]


def _structured_meta_payload(meta: StructuredMeta) -> dict[str, object]:
    """Build the `_meta` dict written into a structured/derived JSON artifact
    (§6). Optional fields are omitted when `None`, except `derived_from`,
    which §6's own example shows present as `[]` rather than omitted."""
    payload: dict[str, object] = {
        "id": meta.id,
        "ticker": meta.ticker,
        "producer": meta.producer,
        "title": meta.title,
        "source": meta.source,
    }
    if meta.url is not None:
        payload["url"] = meta.url
    if meta.request is not None:
        payload["request"] = meta.request
    if meta.provider_tool is not None:
        payload["provider_tool"] = meta.provider_tool
    if meta.fetch_cmd is not None:
        payload["fetch_cmd"] = meta.fetch_cmd
    if meta.fetched_at is not None:
        payload["fetched_at"] = meta.fetched_at
    if meta.computed_at is not None:
        payload["computed_at"] = meta.computed_at
    if meta.generated_at is not None:
        payload["generated_at"] = meta.generated_at
    payload["as_of"] = meta.as_of
    if meta.period is not None:
        payload["period"] = meta.period
    if meta.currency is not None:
        payload["currency"] = meta.currency
    if meta.adjusted is not None:
        payload["adjusted"] = meta.adjusted
    payload["derived_from"] = list(meta.derived_from)
    return payload


def _write_structured_json(target_dir: Path, meta: StructuredMeta, data: object) -> Path:
    """Serialize `{"_meta": ..., "data": ...}` atomically to
    `target_dir / f"{meta.id}.json"`: temp file in `target_dir`, then
    `os.replace` (same pattern as `write_source`). The target path is derived
    here, from `target_dir` and `meta.id` alone, rather than accepted as a
    second caller-supplied argument, so the directory a caller mkdir'd and
    the file it ends up writing into cannot drift apart.

    `allow_nan=False` makes `json.dump` raise `ValueError` on NaN/Infinity
    (§6.4: nulls stay null, never zero-filled, and non-finite floats are not
    valid JSON) instead of silently emitting an unparseable artifact; the
    partially written temp file is discarded and `target` is never touched.
    """
    target = target_dir / f"{meta.id}.json"
    payload = {"_meta": _structured_meta_payload(meta), "data": data}
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=f".{meta.id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, allow_nan=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def write_structured(ticker_dir: Path, meta: StructuredMeta, data: object) -> Path:
    """Write a bronze JSON artifact to `structured/<id>.json` (§6, §20).

    Only `fetch` and `compute` producers are accepted — `model` output is
    silver and must go through `write_derived` instead; this is the layer
    boundary that keeps model-generated content out of a directory citations
    resolve into. Raises `ValueError` if `meta.producer` is not `fetch` or
    `compute`, if the producer's shape (§6.2, `check_fetch_shape`/
    `check_compute_shape`) is unsatisfied, or if `meta.request` carries a
    credential parameter (`check_request_credentials`).

    Overwrite is ALLOWED: unlike `sources/`, structured bronze ids are
    mutable by id (§7 detects a refetch via stamped `derived_from`
    references, not immutability here).

    The file is written atomically (temp file in `structured/`, then
    `os.replace`), and `json.dump(..., allow_nan=False)` refuses NaN/Infinity.
    """
    checker = _SHAPE_CHECKERS.get(meta.producer)
    if checker is None or meta.producer == "model":
        raise ValueError(
            f"write_structured only accepts producer 'fetch' or 'compute' "
            f"(got {meta.producer!r}); model artifacts are silver — use write_derived"
        )
    problems = checker(meta) + check_request_credentials(meta.request)
    if problems:
        raise ValueError(
            f"invalid structured artifact {meta.id!r} (producer={meta.producer!r}): "
            + "; ".join(problems)
        )
    _reject_path_traversal(meta.id, "meta.id")

    structured_dir = ticker_dir / "structured"
    return _write_structured_json(structured_dir, meta, data)


def write_derived(
    ticker_dir: Path, meta: StructuredMeta, data: object, namespace: str | None = None
) -> Path:
    """Write a silver artifact to `derived/<id>.json` or
    `derived/<namespace>/<id>.json` (§4.2, §6, §20).

    Accepts `fetch`, `compute`, and `model` producers — §4.2 is explicit that
    location, not producer shape, sets the layer: a deterministically
    computed artifact written here is silver and non-citable even though the
    same shape under `structured/` would be bronze. This function is kept
    separate from `write_structured` specifically so a silver artifact can
    never reach `structured/` by passing a subdir argument.

    Raises `ValueError` if `meta.producer` is not one of `fetch`/`compute`/
    `model`, if the producer's shape is unsatisfied, or if `meta.request`
    carries a credential parameter. Overwrite is allowed, matching
    `write_structured`. Written atomically with `allow_nan=False`.
    """
    checker = _SHAPE_CHECKERS.get(meta.producer)
    if checker is None:
        raise ValueError(
            f"write_derived: unknown producer {meta.producer!r} "
            f"(expected 'fetch', 'compute', or 'model')"
        )
    problems = checker(meta) + check_request_credentials(meta.request)
    if problems:
        raise ValueError(
            f"invalid derived artifact {meta.id!r} (producer={meta.producer!r}): "
            + "; ".join(problems)
        )
    _reject_path_traversal(meta.id, "meta.id")

    derived_dir = ticker_dir / DERIVED_SUBDIR
    if namespace:
        _reject_path_traversal(namespace, "namespace")
        derived_dir = derived_dir / namespace

    return _write_structured_json(derived_dir, meta, data)


def read_structured(path: Path) -> tuple[StructuredMeta, dict | list]:
    """Read a structured/derived JSON artifact, round-tripping anything
    `write_structured`/`write_derived` wrote. Optional fields omitted from
    `_meta` are filled with `StructuredMeta`'s own defaults (`None`, or `[]`
    for `derived_from`) rather than surfacing as missing-key errors — this is
    the same round-trip contract `read_source` gives `SourceMeta`.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        m = payload["_meta"]
        meta = StructuredMeta(
            id=m["id"],
            ticker=m["ticker"],
            producer=m["producer"],
            title=m["title"],
            source=m["source"],
            as_of=m["as_of"],
            provider_tool=m.get("provider_tool"),
            fetch_cmd=m.get("fetch_cmd"),
            url=m.get("url"),
            request=m.get("request"),
            fetched_at=m.get("fetched_at"),
            computed_at=m.get("computed_at"),
            generated_at=m.get("generated_at"),
            period=m.get("period"),
            currency=m.get("currency"),
            adjusted=m.get("adjusted"),
            derived_from=list(m.get("derived_from") or []),
        )
        data = payload["data"]
    except KeyError as exc:
        # A malformed on-disk artifact (hand-edited, truncated, or missing
        # `_meta`/`data` entirely) must not surface as a bare KeyError:
        # Task 4.1's `validate` (§8.4) is deterministic and fatal WITH A
        # MESSAGE, and this is the one place that message gets attached.
        raise ValueError(
            f"malformed structured artifact {path}: missing required key {exc}"
        ) from exc
    return meta, data
