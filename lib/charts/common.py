#!/usr/bin/env python3
"""Shared reading and formatting helpers for the renderers (spec §16.3, §6.4).

Every renderer is a pure function of persisted artifacts, so they all need the
same two things: a total reader that turns a missing or malformed artifact into
`None` rather than an exception, and formatting that keeps units on the chart.

§6.4's rule shows up here as `MISSING`: a value a provider did not supply stays
absent. It is never interpolated, never zero-filled, and the gap is disclosed in
the caption — a chart that silently bridges a hole in the data is asserting a
figure nobody reported.
"""

from __future__ import annotations

import json
from pathlib import Path

MISSING = None

STRUCTURED = "structured"


def read_artifact(ticker_dir: Path, artifact_id: str) -> dict | None:
    """`<ticker_dir>/structured/<id>.json`'s `data` block, or `None`.

    Total: a missing file, unreadable bytes, invalid JSON or a payload without
    a `data` mapping all return `None`, which the caller turns into §16.1's
    normal degradation.
    """
    path = ticker_dir / STRUCTURED / f"{artifact_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def read_meta(ticker_dir: Path, artifact_id: str) -> dict | None:
    """The artifact's `_meta` block, for provider and as-of in a caption."""
    path = ticker_dir / STRUCTURED / f"{artifact_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = payload.get("_meta") if isinstance(payload, dict) else None
    return meta if isinstance(meta, dict) else None


def read_derived(ticker_dir: Path, namespace: str, artifact_id: str) -> dict | None:
    """A silver artifact's `data` block from `derived/<namespace>/<id>.json`."""
    path = ticker_dir / "derived" / namespace / f"{artifact_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def number(value: object) -> float | None:
    """`value` as a float, or `None` — never 0.0 as a stand-in (§6.4)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result       # NaN is missing, not zero


def money(value: float | None, unit: str = "$") -> str:
    """A magnitude with its unit attached (§17.4: units visible on the chart)."""
    if value is None:
        return "n/a"
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= cut:
            return f"{sign}{unit}{magnitude / cut:.1f}{suffix}"
    return f"{sign}{unit}{magnitude:,.0f}"


def percent(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}%"


def periods_ascending(statement: dict) -> list[str]:
    """A statement's period keys, oldest first. Keys are ISO dates, so sorting
    them lexically is sorting them chronologically."""
    return sorted(k for k in statement if isinstance(k, str))


def statement_series(statement: dict, *names: str) -> dict[str, float | None]:
    """`{period: value}` for the first of `names` present in each period.

    Providers spell one line item several ways, and a period missing the item
    entirely yields `None` — the gap survives to the chart, where
    `connectgaps=False` draws it as a break rather than a straight line through
    data nobody reported.
    """
    out: dict[str, float | None] = {}
    for period in periods_ascending(statement):
        rows = statement.get(period)
        value = None
        if isinstance(rows, dict):
            for name in names:
                value = number(rows.get(name))
                if value is not None:
                    break
        out[period] = value
    return out


def gap_note(series: dict[str, float | None], label: str) -> str | None:
    """A caption clause naming the periods with no reported value (§6.4).

    Disclosure is the point: the chart shows a break, and the caption says
    which periods it covers, so a reader never has to guess whether a hole is
    missing data or a real zero.
    """
    gaps = [period for period, value in series.items() if value is None]
    if not gaps:
        return None
    return f"{label} not reported for {', '.join(gaps)}"
