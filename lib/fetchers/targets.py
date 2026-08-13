#!/usr/bin/env python3
"""Price targets + rating actions and the 4-month recommendation grid."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from lib.fetchers.common import as_float, fetch_cmd, json_safe
from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

MAX_RATING_ACTIONS = 25  # sra5 value: the newest N upgrades/downgrades are the signal


def _yf_targets(ticker: str) -> dict:
    """Default provider: the three yfinance analyst-opinion attributes."""
    import yfinance as yf  # local import: keep the module importable offline

    t = yf.Ticker(ticker)
    return {"price_targets": dict(t.analyst_price_targets or {}),
            "upgrades_downgrades": t.upgrades_downgrades,
            "recommendations": t.recommendations}


def _grid_rows(recs: pd.DataFrame) -> list[dict]:
    """Rating-distribution frame -> json-safe row dicts.

    yfinance returns the grid with `period` as a *column* and a default
    RangeIndex, so resetting the index (as sra5 did) injects a meaningless
    `"index": 0,1,2...` key into every persisted row. Only a labelled index
    carries data worth keeping, so only that one becomes a column.
    """
    rows = recs if isinstance(recs.index, pd.RangeIndex) else recs.reset_index()
    return [{str(k): json_safe(v) for k, v in row.items()} for _, row in rows.iterrows()]


def fetch_targets(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    targets_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Write the price-target distribution + rating grid artifacts, with provenance.

    Two artifacts because they answer different questions: `price_targets_yahoo`
    is the sell-side target spread (with the implied upside a verdict card needs)
    plus who changed their rating and when; `recommendations_yahoo` holds ONLY the
    4-month rating-distribution grid (sra5's CLAUDE.md records that price targets
    never lived in that file, despite what its DAG once claimed).
    Returns sra5's data-function convention: (success, written_paths, error_msg).
    """
    provider = targets_provider or _yf_targets
    now = now or datetime.now(timezone.utc)
    try:
        raw = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"targets fetch failed: {exc}"

    targets = {k: json_safe(v) for k, v in dict(raw.get("price_targets") or {}).items()}
    # Implied upside - the number a verdict card actually needs.
    current = as_float(targets.get("current"))
    if current:
        for key in ("mean", "median", "high", "low"):
            val = as_float(targets.get(key))
            if val is not None:
                targets[f"upside_pct_{key}"] = round((val - current) / current * 100, 1)

    actions: list[dict] = []
    ud = raw.get("upgrades_downgrades")
    if isinstance(ud, pd.DataFrame) and not ud.empty:
        recent = ud.sort_index(ascending=False).head(MAX_RATING_ACTIONS)
        for grade_date, row in recent.iterrows():
            entry = {"date": json_safe(grade_date)}
            entry.update({str(k): json_safe(v) for k, v in row.items()})
            actions.append(entry)

    recs = raw.get("recommendations")
    grid = _grid_rows(recs) if isinstance(recs, pd.DataFrame) and not recs.empty else []

    if not targets and not actions and not grid:
        return False, [], f"no price target data for {ticker.upper()}"

    # Per-attribute warnings, because the all-three-empty guard above is the only
    # thing that used to fire. TOST fetched on 2026-08-12 with an empty
    # `upgrades_downgrades` and populated targets: it persisted as a clean success
    # while the provider held 248 action rows, and the artifact's $25 low — a
    # figure no live analyst held, against 14 post-Q2 raises spanning $34-$45 —
    # reached a published report as though it were current. A partial payload is a
    # degradation (§22.3), so the run continues and the artifacts still write; the
    # caller turns this string into `warnings[kind]`.
    thin = []
    if not targets:
        thin.append("price_targets empty")
    if not actions:
        thin.append("upgrades_downgrades empty — rating actions unavailable, so "
                    "any target low/high in this artifact may be stale")
    if not grid:
        thin.append("recommendations grid empty")

    url = f"https://finance.yahoo.com/quote/{ticker.upper()}/analysis"
    cmd = fetch_cmd(ticker, "targets")
    # No `period`: §6.4 admits only quarterly | annual | ttm, which are statement
    # periods. A target spread and a 4-month rating grid are neither.
    paths = [write_structured(ticker_dir, StructuredMeta(
        id="price_targets_yahoo", ticker=ticker.upper(), producer="fetch",
        title=f"{ticker.upper()} analyst price targets and rating actions",
        source="Yahoo Finance", url=url,
        provider_tool="yfinance.Ticker.analyst_price_targets", fetch_cmd=cmd,
        fetched_at=now.isoformat(), as_of=now.date().isoformat()),
        {"price_targets": targets, "recent_actions": actions})]
    paths.append(write_structured(ticker_dir, StructuredMeta(
        id="recommendations_yahoo", ticker=ticker.upper(), producer="fetch",
        title=f"{ticker.upper()} analyst recommendation grid",
        source="Yahoo Finance", url=url,
        provider_tool="yfinance.Ticker.recommendations", fetch_cmd=cmd,
        fetched_at=now.isoformat(), as_of=now.date().isoformat()),
        {"grid": grid}))

    record_fetch(state, "targets", ["price_targets_yahoo", "recommendations_yahoo"],
                 now, {"policy_days": 7})
    return True, paths, ("; ".join(thin) if thin else None)
