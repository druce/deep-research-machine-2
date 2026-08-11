#!/usr/bin/env python3
"""multpl.com market-aggregate series -> `_MACRO/structured/` (spec §12.2).

These are scraped from an HTML table, so the shape checks are the whole point:
§12.2 requires each scraper to validate expected columns, dtypes, a monotonic
date index and a plausible value range, and says **markup changes must fail
loudly**. A silently-misparsed CAPE would propagate into a valuation section as
though it were evidence.

Failing loudly here is safe because §12.3 makes a failed macro series a WARNING
at the `prefetch-macro` level: the run continues, the series is reported missing,
and nothing downstream mistakes garbage for data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Callable

from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

MULTPL_BASE_URL = "https://www.multpl.com"
HTTP_TIMEOUT = 30

# §12.2: policy_days = 30 for every multpl series.
MULTPL_POLICY_DAYS = 30

# series key -> (url slug, human title, plausible (min, max) range).
# The ranges are sanity bounds, not forecasts: they exist to catch a markup
# change that turns a percentage into a date or an index level, which is the
# failure §12.2 is written against.
MULTPL_SERIES: dict[str, tuple[str, str, tuple[float, float]]] = {
    "sp500_pe": ("s-p-500-pe-ratio", "S&P 500 P/E ratio", (1.0, 200.0)),
    "shiller_pe_cape": ("shiller-pe", "Shiller P/E (CAPE)", (1.0, 100.0)),
    "sp500_dividend_yield": ("s-p-500-dividend-yield", "S&P 500 dividend yield (%)",
                             (0.0, 20.0)),
    "sp500_earnings_yield": ("s-p-500-earnings-yield", "S&P 500 earnings yield (%)",
                             (0.0, 30.0)),
    "sp500_price_real": ("s-p-500-historical-prices", "S&P 500 real price",
                         (1.0, 100_000.0)),
}


class ShapeError(ValueError):
    """The scraped table is not the shape we expect (§12.2: fail loudly)."""


def series_url(slug: str) -> str:
    return f"{MULTPL_BASE_URL}/{slug}/table/by-month"


def _fetch_html(url: str) -> str:
    import httpx  # local import: keep the module importable offline

    resp = httpx.get(url, timeout=HTTP_TIMEOUT,
                     headers={"User-Agent": "sra6/0.1 (research tool)"})
    resp.raise_for_status()
    return resp.text


def parse_table(html: str, value_range: tuple[float, float]) -> list[dict]:
    """Parse a multpl by-month table into `[{date, value}, ...]`, newest first.

    Raises `ShapeError` on anything unexpected: a missing table, absent columns,
    unparseable dates or values, a non-monotonic date index, or a value outside
    the plausible range. Every one of those means the markup moved, and §12.2
    would rather stop than persist a misparse.
    """
    import pandas as pd

    try:
        # flavor="lxml" is pinned deliberately: with no flavor, pandas falls back
        # to html5lib when its first parser finds no table, and html5lib is not a
        # declared dependency — so a missing table would surface as ImportError
        # ("install html5lib") instead of the shape failure it actually is.
        tables = pd.read_html(StringIO(html), flavor="lxml")
    except ValueError as exc:
        raise ShapeError(f"no HTML table found: {exc}") from exc
    if not tables:
        raise ShapeError("no HTML table found")

    df = tables[0]
    if df.shape[1] < 2:
        raise ShapeError(f"expected at least 2 columns, got {df.shape[1]}")
    df = df.iloc[:, :2]
    df.columns = ["date", "value"]

    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().all():
        raise ShapeError("no parseable dates in the first column")
    # Values arrive as "24.53" or "24.53%" or "1,234.56"; anything else is markup drift.
    values = pd.to_numeric(
        df["value"].astype(str).str.replace(r"[,%\s]", "", regex=True),
        errors="coerce")
    if values.isna().all():
        raise ShapeError("no parseable values in the second column")

    keep = dates.notna() & values.notna()
    dates, values = dates[keep], values[keep]
    if dates.empty:
        raise ShapeError("no rows with both a date and a value")

    # multpl serves newest-first; either direction is fine, but a table that is
    # monotonic in NEITHER direction is not a time series.
    if not (dates.is_monotonic_increasing or dates.is_monotonic_decreasing):
        raise ShapeError("date column is not monotonic — table is not a time series")

    low, high = value_range
    out_of_range = values[(values < low) | (values > high)]
    if not out_of_range.empty:
        raise ShapeError(
            f"{len(out_of_range)} value(s) outside the plausible range "
            f"[{low}, {high}] (e.g. {out_of_range.iloc[0]}) — markup likely changed")

    rows = [{"date": d.date().isoformat(), "value": float(v)}
            for d, v in zip(dates, values)]
    return rows if dates.is_monotonic_decreasing else list(reversed(rows))


def fetch_multpl_series(
    series_key: str,
    macro_dir: Path,
    state: dict,
    *,
    html_provider: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch one multpl series into `_MACRO/structured/` (§12.2)."""
    if series_key not in MULTPL_SERIES:
        return False, [], (f"unknown multpl series {series_key!r} "
                           f"(known: {', '.join(sorted(MULTPL_SERIES))})")
    slug, title, value_range = MULTPL_SERIES[series_key]
    provider = html_provider or _fetch_html
    now = now or datetime.now(timezone.utc)
    url = series_url(slug)

    try:
        html = provider(url)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"multpl {series_key} fetch failed: {exc}"
    try:
        rows = parse_table(html, value_range)
    except ShapeError as exc:
        # §12.2: markup changes fail loudly. §12.3 makes that a warning at the
        # prefetch-macro level, so the run continues with the series absent.
        return False, [], f"multpl {series_key} shape check failed: {exc}"

    meta = StructuredMeta(
        id=series_key, ticker="_MACRO", producer="fetch", title=title,
        source="multpl.com", url=url, provider_tool="pandas.read_html",
        fetch_cmd=f"uv run python sra.py prefetch-macro --series {series_key}",
        fetched_at=now.isoformat(),
        # §6.4: as_of is the period end — the newest observation.
        as_of=rows[0]["date"],
        request={"endpoint": url, "params": {}},
    )
    path = write_structured(macro_dir, meta, {"observations": rows})
    record_fetch(state, series_key, series_key, now,
                 {"policy_days": MULTPL_POLICY_DAYS})
    return True, [path], None
