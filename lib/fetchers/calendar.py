#!/usr/bin/env python3
"""Events calendar fetcher + the earnings-date helpers event-driven staleness runs on.

Two views of the same story, and both are needed:

* `yfinance.Ticker.calendar` is purely forward-looking (next earnings window, dividend
  and ex-dividend dates). It answers "what is coming up" for a human reader, which is
  what `next_earnings_date()` serves.
* `yfinance.Ticker.earnings_dates` carries BOTH reported quarters and upcoming
  estimates, so it is the only place a *past* event date can be recovered from. That is
  what event-driven staleness needs: a forward-looking estimate is useless the moment it
  rolls over to the next quarter, while "the last quarter was reported on day X" stays
  true until we refetch (see `lib.statefile.stale_kinds`).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from lib.fetchers.common import fetch_cmd, json_safe
from lib.provenance import StructuredMeta, read_structured, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

ARTIFACT_NAME = "events_calendar_yahoo.json"


def earnings_dates_to_dict(df: pd.DataFrame | None) -> dict[str, dict]:
    """yfinance earnings-dates frame -> {date_iso: {column: value}}.

    Row-major (one entry per quarter), unlike `common.statement_to_dict`'s column-major
    statements. The index is a tz-aware timestamp of the announcement; only its local
    date is kept, since every consumer reasons in whole days.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: dict[str, dict] = {}
    for stamp, row in df.iterrows():
        key = stamp.date().isoformat() if hasattr(stamp, "date") else str(stamp)
        out[key] = {str(k): json_safe(v) for k, v in row.items()}
    return out


def _as_mapping(value: object) -> dict:
    """`value` when it is a dict, else {} - providers and artifacts both lie sometimes."""
    return value if isinstance(value, dict) else {}


def _yf_calendar_data(ticker: str) -> dict:
    """Default provider: the forward calendar plus the reported/estimated quarters."""
    import yfinance as yf  # local import: keep the module importable offline

    t = yf.Ticker(ticker)
    return {"calendar": dict(t.calendar or {}),
            "earnings_dates": earnings_dates_to_dict(t.earnings_dates)}


def fetch_calendar(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    calendar_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Write `structured/events_calendar_yahoo.json`, with provenance.

    The provider returns `{"calendar": {...}, "earnings_dates": {date_iso: {...}}}`;
    either half may be empty (Yahoo drops one or the other for some tickers) and the
    fetch still succeeds - only a completely empty result is an error. A malformed half
    (`None`, a list, anything not a mapping) is read as empty rather than raised, so a
    misbehaving provider degrades to missing data instead of crashing a prefetch run.
    Returns sra5's data-function convention: (success, written_paths, error_msg).
    """
    provider = calendar_provider or _yf_calendar_data
    now = now or datetime.now(timezone.utc)
    try:
        raw = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"calendar fetch failed: {exc}"

    raw = _as_mapping(raw)
    cal = {str(k): json_safe(v) for k, v in _as_mapping(raw.get("calendar")).items()}
    # rows arrive already serialized (see earnings_dates_to_dict); only keys are normalized
    earnings = {str(k): v for k, v in _as_mapping(raw.get("earnings_dates")).items()}
    if not cal and not earnings:
        return False, [], f"no calendar data for {ticker.upper()}"

    meta = StructuredMeta(
        id="events_calendar_yahoo", ticker=ticker.upper(), producer="fetch",
        title=f"{ticker.upper()} events calendar and earnings dates",
        source="Yahoo Finance",
        url=f"https://finance.yahoo.com/quote/{ticker.upper()}",
        provider_tool="yfinance.Ticker.calendar",
        fetch_cmd=fetch_cmd(ticker, "calendar"),
        fetched_at=now.isoformat(), as_of=now.date().isoformat())
    path = write_structured(ticker_dir, meta,
                            {"calendar": cal, "earnings_dates": earnings})
    record_fetch(state, "calendar", "events_calendar_yahoo", now, {"policy_days": 7})
    return True, [path], None


def _read_calendar_data(ticker_dir: Path) -> dict:
    """The artifact's `data` block, or {} when it is missing or unreadable."""
    path = ticker_dir / "structured" / ARTIFACT_NAME
    if not path.exists():
        return {}
    try:
        _, data = read_structured(path)
    except (ValueError, KeyError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _today_utc() -> date:
    """Today in UTC — the single wall-clock read the public helpers make.

    Factored out so the date-filtering logic lives in `_next_earnings_date` /
    `_last_earnings_date`, which take `today` as an argument and are therefore
    testable without patching the clock.
    """
    return datetime.now(timezone.utc).date()


def _next_earnings_date(data: dict, today: date) -> date | None:
    """Earliest upcoming earnings date within `data`, relative to `today`.

    Prefers the forward calendar's `Earnings Date` (returned as-is — it is what Yahoo
    advertises), falling back to the earliest `earnings_dates` key that has not passed
    when that half is absent.
    """
    raw = _as_mapping(data.get("calendar")).get("Earnings Date")
    values = raw if isinstance(raw, list) else [] if raw is None else [raw]
    dates = [d for d in (_as_date(v) for v in values) if d is not None]
    if dates:
        return min(dates)
    upcoming = [d for d in (_as_date(k) for k in _as_mapping(data.get("earnings_dates")))
                if d is not None and d >= today]
    return min(upcoming) if upcoming else None


def _last_earnings_date(data: dict, today: date) -> date | None:
    """Most recent `earnings_dates` key at or before `today` within `data`, else None."""
    rows = _as_mapping(data.get("earnings_dates"))
    past = [d for d in (_as_date(k) for k in rows) if d is not None and d <= today]
    return max(past) if past else None


def next_earnings_date(ticker_dir: Path) -> date | None:
    """Earliest upcoming earnings date from the calendar artifact, or None.

    Prefers the forward calendar's `Earnings Date`, which is what Yahoo actually
    advertises as the next window. When that half is missing - `fetch_calendar` ships a
    partial artifact when Yahoo returns only one of the two - it falls back to the
    earliest `earnings_dates` key that has not passed yet: the mirror image of
    `last_earnings_date`'s past-dates filter, off the same rows.

    Human-facing ("next earnings in N days"); staleness uses `last_earnings_date`.
    """
    return _next_earnings_date(_read_calendar_data(ticker_dir), _today_utc())


def last_earnings_date(ticker_dir: Path) -> date | None:
    """Most recent earnings date that is already in the past (or today), else None.

    This is the event signal `lib.statefile.stale_kinds` consumes for the `on_earnings`
    policy. The filter is the date itself rather than a non-null `Reported EPS`, because
    Yahoo backfills the reported figure some time after the announcement - the date
    having arrived is what makes a stored fundamentals artifact out of date.
    """
    return _last_earnings_date(_read_calendar_data(ticker_dir), _today_utc())
