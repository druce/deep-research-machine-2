#!/usr/bin/env python3
"""Peers source 2: which funds hold the subject, and what else they hold.

`etf_equity_exposure` returns ~2000 funds for a large-cap name, topped by
leveraged single-stock products, broad rotation funds with thousands of
holdings, and foreign listings of funds already counted. The filters here are
what make the source usable; see the design doc's "Source 2 detail" section.
"""
from __future__ import annotations

from typing import Callable

from lib.fmp_http import fmp_get

FMP_ETF_INFO_URL = "https://financialmodelingprep.com/stable/etf/info"
HTTP_TIMEOUT = 30

MAX_SUBJECT_WEIGHT = 0.25   # above this the fund is leveraged or single-stock
MIN_HOLDINGS = 20           # below this it is not a diversified industry fund
MAX_HOLDINGS = 150          # above this it is a broad-market fund
TOP_FUNDS = 5
TOP_HOLDINGS_PER_FUND = 50


def prefilter_exposure(rows: list[dict]) -> list[dict]:
    """Drop unusable funds and sort the rest by weight, descending.

    Dropped: null weights, weights above MAX_SUBJECT_WEIGHT (leveraged and
    single-stock funds), and symbols containing "." or " " (foreign listings
    such as CIBR.L and "WCBR LN", which duplicate their US lines).
    """
    kept = [
        r for r in rows
        if r.get("weight") is not None
        and r["weight"] <= MAX_SUBJECT_WEIGHT
        and "." not in str(r.get("etf_symbol", ""))
        and " " not in str(r.get("etf_symbol", ""))
    ]
    return sorted(kept, key=lambda r: -r["weight"])


def select_funds(
    candidates: list[dict],
    info_fn: Callable[[str], int | None],
    top_n: int = TOP_FUNDS,
    failed: list[str] | None = None,
) -> list[dict]:
    """Walk prefiltered candidates, keep those inside the holdings band, stop at top_n.

    `info_fn(symbol)` returns the fund's holdings count, or None when unknown.
    A lookup that raises is treated as unknown: one flaky fund must not cost us
    the whole source.

    `failed` is an optional sink: every symbol whose lookup raised is appended to
    it. Without it, /etf/info being down for EVERY fund is indistinguishable from
    "no fund fell inside the band" -- an empty list either way, reported as
    success. `fetch_peers` passes a sink and turns a total outage into a warning.
    """
    picked: list[dict] = []
    for row in candidates:
        if len(picked) >= top_n:
            break
        symbol = row["etf_symbol"]
        try:
            count = info_fn(symbol)
        except Exception:  # noqa: BLE001 - provider errors are data, not crashes
            if failed is not None:
                failed.append(str(symbol))
            continue
        if count is None or not (MIN_HOLDINGS <= count <= MAX_HOLDINGS):
            continue
        picked.append({"symbol": symbol, "weight": row["weight"],
                       "holdings_count": count})
    return picked


def union_holdings(
    fund_holdings: dict[str, list[dict]],
    top_k: int = TOP_HOLDINGS_PER_FUND,
) -> dict[str, int]:
    """Map symbol -> how many funds hold it, over each fund's top_k by weight.

    Cash lines ($USD, $CAD), rows with no weight, and rows with NO SYMBOL are
    ignored. The last one matters: FMP's holdings feed carries a null `asset` on
    cash, derivative and unmapped lines, and `str(None).upper()` is the literal
    string "NONE" -- which used to clear both filters and reach the rater as a
    fabricated ticker.
    """
    counts: dict[str, int] = {}
    for holdings in fund_holdings.values():
        rows = [h for h in holdings
                if h.get("symbol")
                and h.get("weight") is not None
                and not str(h["symbol"]).startswith("$")]
        rows.sort(key=lambda h: -h["weight"])
        for h in rows[:top_k]:
            symbol = str(h["symbol"]).strip().upper()
            counts[symbol] = counts.get(symbol, 0) + 1
    return counts


def fmp_etf_holdings_count(symbol: str) -> int | None:
    """Default info_fn: holdingsCount from FMP /stable/etf/info.

    None means "the fund answered but reports no holdings count" (a leveraged
    ETN such as FNGU). Everything else RAISES, and `select_funds` counts those
    separately, so a whole-source outage cannot masquerade as "no fund fell
    inside the band".

    That includes an EMPTY answer: `200 []` for a symbol the exposure feed just
    named is the endpoint failing to answer, not data -- it is the `/etf/holder`
    404 failure mode wearing a 200, and it was being reported as a band
    rejection.
    """
    payload = fmp_get(FMP_ETF_INFO_URL, {"symbol": symbol}, timeout=HTTP_TIMEOUT)
    if not payload or not isinstance(payload[0], dict):
        raise RuntimeError(f"FMP info returned no usable row for {symbol}")
    return payload[0].get("holdingsCount")
