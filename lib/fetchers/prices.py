#!/usr/bin/env python3
"""Prices fetcher: 4y daily OHLCV for the ticker plus the S&P 500 benchmark (§11.1)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from lib.fetchers.common import fetch_cmd, json_safe
from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

PRICES_YEARS = 4
BENCHMARK_SYMBOL = "^GSPC"
REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

# §6.4: `_meta.adjusted` records whether prices are split/dividend adjusted, and
# per-share figures must not mix conventions. yfinance's `download` returns the
# provider's adjusted series by default, and §6.4 says to store the provider's
# convention rather than re-deriving one.
PRICES_ADJUSTED = True


def _validate_ohlcv(df: pd.DataFrame) -> str | None:
    """None if `df` is a serializable OHLCV frame, else a reason.

    `_frame_to_series` indexes the five OHLCV columns and calls `.date()` on
    every index entry, so a provider frame missing a column or carrying a
    non-datetime index would raise mid-serialization. Checking up front turns a
    malformed provider into the fetcher's `(False, [], msg)` contract instead of
    a traceback out of a prefetch run.
    """
    missing = [c for c in REQUIRED_PRICE_COLUMNS if c not in df.columns]
    if missing:
        return f"missing OHLCV columns: {', '.join(missing)}"
    if not isinstance(df.index, pd.DatetimeIndex):
        return f"index is not date-like (got {type(df.index).__name__})"
    return None


def _yf_daily_history(symbol: str) -> pd.DataFrame:
    import yfinance as yf  # local import: keep the module importable offline

    df = yf.download(symbol, period=f"{PRICES_YEARS}y", interval="1d", progress=False)
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df if df is not None else pd.DataFrame()


def _frame_to_series(df: pd.DataFrame) -> dict:
    """OHLCV frame -> column-oriented json-safe dict (what technical/ charts read)."""
    return {
        "dates": [ts.date().isoformat() for ts in df.index],
        "open": [json_safe(v) for v in df["Open"]],
        "high": [json_safe(v) for v in df["High"]],
        "low": [json_safe(v) for v in df["Low"]],
        "close": [json_safe(v) for v in df["Close"]],
        "volume": [json_safe(v) for v in df["Volume"]],
    }


def fetch_prices(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    prices_provider: Callable[[str], pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch 4y daily OHLCV plus the S&P 500 benchmark, with provenance.

    A benchmark failure is tolerated — the chart's relative-strength panel is
    optional — while a ticker failure fails the whole fetch.
    """
    provider = prices_provider or _yf_daily_history
    now = now or datetime.now(timezone.utc)
    try:
        df = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"prices fetch failed: {exc}"
    if df is None or df.empty:
        return False, [], f"no price data for {ticker}"
    invalid = _validate_ohlcv(df)
    if invalid:
        return False, [], f"malformed price data for {ticker}: {invalid}"

    benchmark: dict | None
    try:
        bench = provider(BENCHMARK_SYMBOL)
        if bench is None or bench.empty:
            benchmark = None
        else:
            benchmark = {
                "symbol": BENCHMARK_SYMBOL,
                "dates": [ts.date().isoformat() for ts in bench.index],
                "close": [json_safe(v) for v in bench["Close"]],
            }
    except Exception:  # benchmark is optional — the RS panel degrades gracefully
        benchmark = None

    data = {"daily": _frame_to_series(df), "benchmark": benchmark}
    meta = StructuredMeta(
        id="prices_yahoo",
        ticker=ticker.upper(),
        producer="fetch",
        title=f"{ticker.upper()} daily OHLCV prices ({PRICES_YEARS}y)",
        source="Yahoo Finance",
        url=f"https://finance.yahoo.com/quote/{ticker.upper()}/history",
        provider_tool="yfinance.download",
        fetch_cmd=fetch_cmd(ticker, "prices"),
        fetched_at=now.isoformat(),
        # §6.4: as_of is the period end — the last bar, not the fetch time.
        as_of=data["daily"]["dates"][-1],
        # No `period`: §6.4 enumerates it as quarterly | annual | ttm — those are
        # financial-STATEMENT periods. A price series has a bar interval, not a
        # statement period, and stuffing "daily" into the field would put a value
        # there that §6.4 does not admit. The interval lives in the title and in
        # the data's own shape.
        adjusted=PRICES_ADJUSTED,
    )
    path = write_structured(ticker_dir, meta, data)
    record_fetch(state, "prices", "prices_yahoo", now, {"policy_days": 1})
    return True, [path], None
