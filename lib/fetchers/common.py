#!/usr/bin/env python3
"""Shared helpers for provenance-emitting fetchers (JSON coercion, supersedes lookup)."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pandas as pd

from lib.provenance import read_source, resolve_source

ESTIMATE_PERIOD_LABELS: dict[str, str] = {
    "0q": "current_quarter",
    "+1q": "next_quarter",
    "0y": "current_fiscal_year",
    "+1y": "next_fiscal_year",
}


def json_safe(value) -> object:
    """Coerce a pandas/numpy/datetime scalar into something json.dump accepts."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    # NaN is caught above; ±Infinity is not "missing" to pandas but is equally
    # unrepresentable in JSON, and write_structured now refuses it outright.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):  # numpy scalar
        try:
            item = value.item()
        except (ValueError, AttributeError):
            pass
        else:
            if isinstance(item, float) and not math.isfinite(item):
                return None
            return item
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def as_float(value) -> float | None:
    """Narrow a json_safe() result to float, or None.

    json_safe returns a union (numbers, ISO strings, lists), so every arithmetic
    use downstream has to narrow it first.
    """
    if value is None or isinstance(value, (list, tuple, dict, bool)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def frame_by_period(df: pd.DataFrame | None, labels: dict[str, str]) -> dict:
    """yfinance estimate frame (indexed 0q/+1q/0y/+1y) -> dict keyed by human label."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: dict = {}
    for period, row in df.iterrows():
        label = labels.get(str(period), str(period))
        out[label] = {str(k): json_safe(v) for k, v in row.items()}
    return out


def statement_to_dict(df: pd.DataFrame) -> dict[str, dict]:
    """Statement frame (metric rows x period columns) -> {period_iso: {metric: value}}."""
    out: dict[str, dict] = {}
    for col in df.columns:
        key = col.date().isoformat() if isinstance(col, pd.Timestamp) else str(col)
        out[key] = {str(idx): json_safe(v) for idx, v in df[col].items()}
    return out


def find_prior_source(ticker_dir: Path, stem_suffix: str) -> str | None:
    """Latest CURRENT sources/<YYYY-MM-DD>_<stem_suffix>.md stem, else None.

    Sources are immutable, so a refetch writes a new dated file that points back at
    the prior one via `supersedes:`.

    Deliberately does not look in `sources/archive/`: `supersedes:` must name the
    document being replaced, and an archived file has already been replaced.
    Pointing a new source at one would chain onto a dead link while leaving the
    live document unarchived (§5).
    """
    pattern = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}_{re.escape(stem_suffix)}$")
    stems = sorted(
        p.stem for p in (ticker_dir / "sources").glob("*.md") if pattern.match(p.stem)
    )
    return stems[-1] if stems else None


def source_exists(ticker_dir: Path, source_id: str) -> bool:
    """True if `source_id` is taken, current OR archived (immutability guard).

    Resolves through `resolve_source` so this agrees with `write_source`, which
    raises `FileExistsError` for an id taken in either directory (§5: ids are
    unique across both). Checking only `sources/` would let a caller conclude
    "safe to write" and then crash on an id archived earlier.
    """
    return resolve_source(ticker_dir, source_id) is not None


def validate_existing_source(
        ticker_dir: Path, source_id: str, expected_kind: str) -> str | None:
    """None if sources/<source_id>.md reads cleanly and matches id+kind, else a reason.

    The immutable same-day no-op paths (news/wikipedia/transcript) treat an existing
    day-stamped file as already-current and stamp freshness without re-fetching. But
    if that file is empty, corrupt, or somehow the wrong document, a blind no-op would
    record the kind fresh over garbage. This checks the file is a readable source doc
    of the expected id and kind first; the caller turns a non-None reason into a clean
    failure, and never overwrites the immutable file either way.
    """
    path = ticker_dir / "sources" / f"{source_id}.md"
    try:
        meta, body = read_source(path)
    except Exception as exc:  # empty/corrupt/unparseable frontmatter
        return f"unreadable ({type(exc).__name__})"
    if meta.id != source_id:
        return f"id mismatch (found {meta.id!r}, expected {source_id!r})"
    if meta.kind != expected_kind:
        return f"kind mismatch (found {meta.kind!r}, expected {expected_kind!r})"
    if not body.strip():
        return "empty body"
    return None
