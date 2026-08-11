#!/usr/bin/env python3
"""Analyst estimates fetcher: forward consensus + revision breadth and direction."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import (
    ESTIMATE_PERIOD_LABELS, as_float, fetch_cmd, frame_by_period)
from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()


def revision_deltas(trend: dict) -> dict:
    """Add revision_pct_{7d,30d,60d,90d} to each eps_trend row.

    This is the whole point of fetching eps_trend: a report needs to say "FY26
    consensus EPS has been revised up 39% over 90 days", not print five
    undifferentiated numbers and leave the reader to do the arithmetic.

    Ported verbatim from sra5 skills/fetch_fundamental/fetch_fundamental.py
    _revision_deltas (only the helper name _as_float -> as_float changed).
    """
    out = {}
    for label, row in trend.items():
        current = as_float(row.get("current"))
        deltas = {}
        for ago_key, name in (("7daysAgo", "7d"), ("30daysAgo", "30d"),
                              ("60daysAgo", "60d"), ("90daysAgo", "90d")):
            prior = as_float(row.get(ago_key))
            if current is None or not prior:
                continue
            deltas[f"revision_pct_{name}"] = round((current - prior) / abs(prior) * 100, 1)
        out[label] = {**row, **deltas} if deltas else row
    return out


def _yf_estimates(ticker: str) -> dict:
    """Default provider: the four yfinance analyst-estimate frames."""
    import yfinance as yf  # local import: keep the module importable offline

    t = yf.Ticker(ticker)
    return {"earnings_estimate": t.earnings_estimate,
            "revenue_estimate": t.revenue_estimate,
            "eps_revisions": t.eps_revisions,
            "eps_trend": t.eps_trend}


def fetch_estimates(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    estimates_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Write forward consensus + EPS revision artifacts, with provenance.

    Two artifacts because they answer different questions and Yahoo shows both on
    the same /analysis page: `estimates_yahoo` is the consensus level (and how far
    it has moved), `eps_revisions_yahoo` is the breadth of analysts moving each way.
    Returns sra5's data-function convention: (success, written_paths, error_msg).
    """
    provider = estimates_provider or _yf_estimates
    now = now or datetime.now(timezone.utc)
    try:
        raw = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"estimates fetch failed: {exc}"

    earnings = frame_by_period(raw.get("earnings_estimate"), ESTIMATE_PERIOD_LABELS)
    revenue = frame_by_period(raw.get("revenue_estimate"), ESTIMATE_PERIOD_LABELS)
    revisions = frame_by_period(raw.get("eps_revisions"), ESTIMATE_PERIOD_LABELS)
    trend = revision_deltas(frame_by_period(raw.get("eps_trend"), ESTIMATE_PERIOD_LABELS))
    if not any([earnings, revenue, revisions, trend]):
        return False, [], f"no analyst estimate data for {ticker.upper()}"

    url = f"https://finance.yahoo.com/quote/{ticker.upper()}/analysis"
    cmd = fetch_cmd(ticker, "estimates")
    # No `period`: §6.4 admits only quarterly | annual | ttm. These are FORWARD
    # consensus figures spanning several future periods at once, which is not one
    # of those; the per-period breakdown is the data's own keys.
    paths = [write_structured(ticker_dir, StructuredMeta(
        id="estimates_yahoo", ticker=ticker.upper(), producer="fetch",
        title=f"{ticker.upper()} analyst consensus estimates",
        source="Yahoo Finance", url=url,
        provider_tool="yfinance.Ticker.earnings_estimate", fetch_cmd=cmd,
        fetched_at=now.isoformat(), as_of=now.date().isoformat()),
        {"earnings_estimate": earnings, "revenue_estimate": revenue, "eps_trend": trend})]
    paths.append(write_structured(ticker_dir, StructuredMeta(
        id="eps_revisions_yahoo", ticker=ticker.upper(), producer="fetch",
        title=f"{ticker.upper()} EPS revision breadth",
        source="Yahoo Finance", url=url,
        provider_tool="yfinance.Ticker.eps_revisions", fetch_cmd=cmd,
        fetched_at=now.isoformat(), as_of=now.date().isoformat()),
        {"eps_revisions": revisions}))

    # Both ids, so a missing artifact for either makes the kind stale (§10.1).
    record_fetch(state, "estimates", ["estimates_yahoo", "eps_revisions_yahoo"], now,
                 {"policy_days": 7})
    return True, paths, None
