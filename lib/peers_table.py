#!/usr/bin/env python3
"""Merging the four peer sources into one table, and the two row filters."""
from __future__ import annotations

MIN_FUND_OVERLAP = 2   # a funds-only candidate must appear in at least this many

# Canonical order for the `sources` list on each row.
_SOURCE_ORDER = ("user", "fmp_peers", "proxy", "funds")


def _norm(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def merge_candidates(
    subject: str,
    user: list[str],
    fmp: list[str],
    proxy: list[dict],
    fund_counts: dict[str, int],
) -> list[dict]:
    """One row per distinct symbol, recording which sources named it.

    The subject is always present with is_subject=True so raters can size every
    candidate against it; both filters below keep that row, and
    `peers_scoring.apply_selection` excludes it from the selected set.
    """
    subject = _norm(subject)
    named: dict[str, set[str]] = {}
    names: dict[str, str] = {}

    for symbol in user:
        named.setdefault(_norm(symbol), set()).add("user")
    for symbol in fmp:
        named.setdefault(_norm(symbol), set()).add("fmp_peers")
    for row in proxy:
        symbol = _norm(row.get("symbol"))
        named.setdefault(symbol, set()).add("proxy")
        if row.get("name"):
            names[symbol] = str(row["name"])
    for symbol in fund_counts:
        named.setdefault(_norm(symbol), set()).add("funds")
    named.setdefault(subject, set())

    rows: list[dict] = []
    for symbol, sources in named.items():
        if not symbol:
            continue
        rows.append({
            "symbol": symbol,
            "name": names.get(symbol, ""),
            "fund_count": int(fund_counts.get(symbol, 0)),
            "sources": [s for s in _SOURCE_ORDER if s in sources],
            "is_subject": symbol == subject,
        })
    # subject first, then most-corroborated first, then alphabetical for stability
    rows.sort(key=lambda r: (not r["is_subject"], -len(r["sources"]),
                             -r["fund_count"], r["symbol"]))
    return rows


def overlap_filter(rows: list[dict], min_funds: int = MIN_FUND_OVERLAP) -> list[dict]:
    """Drop the single-fund long tail that no other source corroborates.

    An irrelevant fund in the top 5 (a biothreat or defense fund that happens to
    hold the subject) contributes holdings that appear in exactly one fund and
    nowhere else. Requiring two funds -- unless FMP, the proxy or the user named
    it -- removes them without needing to judge the funds themselves.
    """
    return [
        r for r in rows
        if r["is_subject"]
        or r["fund_count"] >= min_funds
        or any(s in r["sources"] for s in ("user", "fmp_peers", "proxy"))
    ]


def hygiene_filter(rows: list[dict]) -> list[dict]:
    """Drop rows that are not live operating companies.

    ETFs and funds are not comparables, and a delisted shell (an acquired
    company such as SPLK or MNDT) must never reach a rater. Unknown flags are
    kept: a failed profile lookup should not silently delete a candidate.

    The subject is exempt, exactly as in `overlap_filter`: a subject whose FMP
    profile says isActivelyTrading=false would otherwise be deleted from its own
    candidate table, leaving the rater nothing to size against and letting
    `apply_selection` (whose subject set is read from these rows) rank the
    subject as its own peer.
    """
    return [
        r for r in rows
        if r.get("is_subject")
        or (not r.get("is_etf")
            and not r.get("is_fund")
            and r.get("is_actively_trading") is not False)
    ]
