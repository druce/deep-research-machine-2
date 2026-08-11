#!/usr/bin/env python3
"""Enrichment for the peer candidate table: classification, size, description.

FMP's taxonomy is NOT GICS -- the real four-tier S&P/MSCI scheme is licensed
data. Columns are named fmp_sector / fmp_industry so no artifact ever claims
otherwise. `description` is carried so raters judge business overlap from what
a company actually does rather than from its ticker.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from lib.fmp_http import fmp_get

FMP_PROFILE_URL = "https://financialmodelingprep.com/stable/profile"
FMP_INCOME_URL = "https://financialmodelingprep.com/stable/income-statement"
HTTP_TIMEOUT = 30

# /stable/profile takes ONE symbol. A comma-separated list returns HTTP 200 with
# an empty array -- no error, no warning -- so a batching implementation yields
# no profiles at all and every candidate ends up with null classification, null
# market cap and an empty description. Verified live 2026-08-01:
#   ?symbol=CRWD       -> 200, 1 row
#   ?symbol=CRWD,PANW  -> 200, []
# So profiles are fetched one per symbol and parallelised, like revenues.
PROFILE_WORKERS = 8
REVENUE_WORKERS = 8     # revenue is one call per candidate; bound the fan-out
TTM_QUARTERS = 4        # trailing twelve months = the last four quarterly reports


def _fmp_profile(symbol: str) -> list[dict]:
    """One symbol's profile. See PROFILE_WORKERS for why this is not batched."""
    return fmp_get(FMP_PROFILE_URL, {"symbol": symbol}, timeout=HTTP_TIMEOUT)


def _fmp_income(symbol: str) -> list[dict]:
    """The last four QUARTERLY income statements, most recent first.

    Quarterly rather than annual because peers keep different fiscal calendars
    -- CRWD's year ends 31 Jan, PANW's 31 Jul, TOST's 31 Dec -- so comparing
    "most recent annual" compares different twelve-month windows, which is
    precisely what the size_proximity axis is supposed to measure. Summing four
    quarters puts every peer on the same window at no extra API cost.
    """
    return fmp_get(FMP_INCOME_URL,
                   {"symbol": symbol, "period": "quarter", "limit": TTM_QUARTERS},
                   timeout=HTTP_TIMEOUT)


def fetch_profiles(
    symbols: list[str],
    profile_fn: Callable[[str], list[dict]] | None = None,
    max_workers: int = PROFILE_WORKERS,
    failed: list[str] | None = None,
) -> dict[str, dict]:
    """symbol -> FMP profile row, one call per symbol, parallelised. Never raises.

    A symbol whose lookup fails, returns nothing, or returns something that is
    not a list of dicts is simply absent from the result; `enrich` then nulls
    that row's columns and `hygiene_filter` keeps it, so a flaky lookup degrades
    one candidate rather than deleting it.

    The response parsing lives INSIDE the try for the same reason the call does:
    a malformed payload used to raise past `fetch_peers`' un-wrapped enrichment
    block and traceback out of `sra.py peers-candidates`, which owes the caller
    an exit code.

    `failed` is an optional sink (same contract as `peers_funds.select_funds`):
    every symbol whose lookup RAISED is appended to it. Degrading quietly is
    right per-symbol and wrong in bulk -- an expired key or a 429 burst across
    the pool nulls every column the rater judges on, which is exactly the shape
    of the /stable/profile batching bug this branch already shipped. Without the
    sink the caller cannot tell that from "these symbols have no profile".
    """
    profile_fn = profile_fn or _fmp_profile

    def one(symbol: str) -> tuple[str, dict | None, bool]:
        try:
            rows = profile_fn(symbol)
            if not isinstance(rows, list) or not rows:
                return symbol, None, False
            return symbol, (rows[0] if isinstance(rows[0], dict) else None), False
        except Exception:  # noqa: BLE001 - provider errors are data, not crashes
            return symbol, None, True

    if not symbols:
        return {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(one, symbols))
    # key off the response's own symbol when present, so a provider that
    # normalises the ticker cannot silently misalign the mapping
    out: dict[str, dict] = {}
    for requested, row, raised in results:
        if raised and failed is not None:
            failed.append(requested)
        if not row:
            continue
        symbol = str(row.get("symbol") or requested).strip().upper()
        out[symbol] = row
    return out


def fetch_revenues(
    symbols: list[str],
    income_fn: Callable[[str], list[dict]] | None = None,
    max_workers: int = REVENUE_WORKERS,
    failed: list[str] | None = None,
) -> dict[str, float | None]:
    """symbol -> trailing-twelve-month revenue, or None. Never raises.

    None rather than a partial sum whenever fewer than TTM_QUARTERS quarters
    came back or any of them lacks a usable revenue figure: a recent IPO with
    three quarters would otherwise report a number ~25% low, and a quietly wrong
    figure is worse for peer sizing than a missing one.

    As in `fetch_profiles`, the parsing is inside the try: a stringified or
    non-numeric revenue, or a payload of non-dicts, is a malformed response to
    report as "unknown", not a TypeError raised through `fetch_peers`. And as in
    `fetch_profiles`, `failed` collects the symbols whose lookup RAISED so the
    caller can tell a dead endpoint from genuinely missing figures.
    """
    income_fn = income_fn or _fmp_income

    def one(symbol: str) -> tuple[str, float | None, bool]:
        try:
            rows = income_fn(symbol)
            if not isinstance(rows, list):
                return symbol, None, False
            quarters = [r.get("revenue") for r in rows[:TTM_QUARTERS]
                        if isinstance(r, dict)]
            if len(quarters) < TTM_QUARTERS:
                return symbol, None, False
            # float(q) per quarter: a stringified figure is coerced, a
            # non-numeric one lands in the except below as "unknown".
            total = 0.0
            for quarter in quarters:
                if quarter is None:
                    return symbol, None, False
                total += float(quarter)
            return symbol, total, False
        except Exception:  # noqa: BLE001 - provider errors are data, not crashes
            return symbol, None, True

    if not symbols:
        return {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(one, symbols))
    if failed is not None:
        failed.extend(symbol for symbol, _value, raised in results if raised)
    return {symbol: value for symbol, value, _raised in results}


def enrich(
    rows: list[dict],
    profiles: dict[str, dict],
    revenues: dict[str, float | None],
) -> list[dict]:
    """Attach classification, size, description and hygiene flags to each row.

    A candidate with no profile keeps its row with null columns -- the hygiene
    filter treats unknown flags as "keep", so a flaky lookup never silently
    deletes a peer.
    """
    out: list[dict] = []
    for row in rows:
        p = profiles.get(row["symbol"], {})
        out.append({
            **row,
            "name": p.get("companyName") or row.get("name") or "",
            "fmp_sector": p.get("sector"),
            "fmp_industry": p.get("industry"),
            "country": p.get("country"),
            "exchange": p.get("exchange"),
            "market_cap": p.get("marketCap"),
            "revenue_ttm": revenues.get(row["symbol"]),
            "description": p.get("description") or "",
            "is_etf": p.get("isEtf"),
            "is_fund": p.get("isFund"),
            "is_actively_trading": p.get("isActivelyTrading"),
        })
    return out
