#!/usr/bin/env python3
"""Fundamentals fetcher: three annual statements plus a key-ratios artifact.

Each statement is its own artifact so a citation resolves to the one Yahoo page
that shows it (§6.3 source separation).

A note on `key_ratios_computed` and §6.4. The ratio VALUES come from Yahoo's
`info` snapshot — they are Yahoo's own TTM and market-derived figures, not
arithmetic this driver performs. That satisfies §6.4's two hard rules: no
cross-provider arithmetic (every figure comes from one provider), and the
driver never constructs TTM from four quarters (TTM is used only as supplied).
The artifact is stamped `compute` with `derived_from` naming the same-provider
artifacts a reader should check it against — the statements written in this run,
plus `profile_yahoo` when it exists, which comes from the same `info` snapshot.
Nulls stay null throughout: a missing ratio is absent data, never zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from lib.fetchers.common import fetch_cmd, json_safe, statement_to_dict
from lib.provenance import SOURCE_COMPUTED, StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

# snake_case metric -> yfinance `info` key. Field selection ported from sra5's
# get_financial_ratios (same 5 categories / 29 metrics), storing raw values
# rather than display strings.
RATIO_FIELDS: dict[str, dict[str, str]] = {
    "valuation": {
        "trailing_pe": "trailingPE", "forward_pe": "forwardPE", "peg_ratio": "pegRatio",
        "price_to_sales_ttm": "priceToSalesTrailing12Months", "price_to_book": "priceToBook",
        "ev_to_revenue": "enterpriseToRevenue", "ev_to_ebitda": "enterpriseToEbitda"},
    "highlights": {
        "market_cap": "marketCap", "enterprise_value": "enterpriseValue",
        "revenue_ttm": "totalRevenue", "ebitda": "ebitda",
        "net_income": "netIncomeToCommon", "total_cash": "totalCash",
        "total_debt": "totalDebt", "revenue_growth_yoy": "revenueGrowth",
        "earnings_growth_yoy": "earningsGrowth"},
    "profitability": {
        "gross_margin": "grossMargins", "operating_margin": "operatingMargins",
        "profit_margin": "profitMargins", "return_on_assets": "returnOnAssets",
        "return_on_equity": "returnOnEquity"},
    "liquidity": {
        "current_ratio": "currentRatio", "quick_ratio": "quickRatio",
        "debt_to_equity": "debtToEquity"},
    "per_share": {
        "eps_ttm": "trailingEps", "eps_forward": "forwardEps", "book_value": "bookValue",
        "revenue_per_share": "revenuePerShare", "dividend_rate": "dividendRate"},
}

# Divergence above this between Yahoo's `enterpriseValue` and the same snapshot's
# own market cap, debt and cash is reported as a warning. 2% is wide enough to
# absorb an intraday market-cap refresh and narrow enough that TOST's 5% gap fires.
EV_DIVERGENCE_TOLERANCE = 0.02


def _add_computed_ev(ratios: dict) -> str | None:
    """Add an EV computed from the snapshot's own inputs. Returns a warning.

    Yahoo's `enterpriseValue` is passed through, and on 2026-08-12 TOST's
    contradicted the rest of the same snapshot: market cap $19,287.9M less EV
    $18,447.6M implies $840M of net cash, while the artifact's own `total_cash`
    read $1,713.0M against `total_debt` of $0 — which is what the 10-Q shows
    ($1,015M cash plus $698M securities, no funded debt). Add EV back to the
    filed net cash and you exceed the actual market cap by $873M, so the
    provider's EV and its own cash line cannot both be right. Every EV multiple
    downstream inherited the gap, and a synthesizer three stages later was what
    caught it.

    This stays additive on purpose. §6.4 and this module's docstring make
    `key_ratios_computed` a pass-through of the provider's own ratios, so
    silently rebasing `ev_to_revenue` would leave the artifact reporting numbers
    that disagree with the provider while still looking like it. The computed
    figures sit beside the provider's under `_computed` names; a writer picks,
    and the warning tells the operator there is a choice to make. All three
    inputs come from one `info` snapshot, so this is same-provider arithmetic.
    """
    highlights, valuation = ratios["highlights"], ratios["valuation"]
    market_cap = highlights.get("market_cap")
    total_debt = highlights.get("total_debt")
    total_cash = highlights.get("total_cash")
    if market_cap is None or total_debt is None or total_cash is None:
        return None

    computed = market_cap + total_debt - total_cash
    highlights["enterprise_value_computed"] = computed

    revenue, ebitda = highlights.get("revenue_ttm"), highlights.get("ebitda")
    valuation["ev_to_revenue_computed"] = (
        round(computed / revenue, 3) if revenue else None)
    valuation["ev_to_ebitda_computed"] = (
        round(computed / ebitda, 3) if ebitda else None)

    provider_ev = highlights.get("enterprise_value")
    if not provider_ev or not computed:
        return None
    if abs(provider_ev - computed) / abs(computed) <= EV_DIVERGENCE_TOLERANCE:
        return None
    return (f"enterprise_value: provider reports {provider_ev:,.0f} but its own "
            f"market cap, debt and cash imply {computed:,.0f} "
            f"({(provider_ev - computed) / computed * 100:+.1f}%) — "
            f"ev_to_revenue and ev_to_ebitda inherit the provider figure; "
            f"the *_computed fields carry the reconciled one")


# (provider key, artifact id, Yahoo URL slug, provider_tool).
_STATEMENTS = (
    ("income_stmt", "income_statement_yahoo", "financials", "yfinance.Ticker.income_stmt"),
    ("balance_sheet", "balance_sheet_yahoo", "balance-sheet", "yfinance.Ticker.balance_sheet"),
    ("cashflow", "cashflow_yahoo", "cash-flow", "yfinance.Ticker.cashflow"),
)


def _yf_financials(ticker: str) -> dict:
    """Default provider: the three annual statements plus the info snapshot."""
    import yfinance as yf  # local import: keep the module importable offline

    t = yf.Ticker(ticker)
    return {"income_stmt": t.income_stmt, "balance_sheet": t.balance_sheet,
            "cashflow": t.cashflow, "info": t.info or {}}


def fetch_financials(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    financials_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Write the three annual statements plus key ratios, with provenance.

    A statement the provider omits is skipped, and its absence from the ratios
    artifact's `derived_from` is what records that it did not contribute. All
    three missing is a failure, so the ratios are never stamped with an empty
    lineage — which `validate` would reject anyway (§6.2: `compute` requires a
    non-empty `derived_from`).
    """
    provider = financials_provider or _yf_financials
    now = now or datetime.now(timezone.utc)
    try:
        raw = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"financials fetch failed: {exc}"

    paths: list[Path] = []
    written_ids: list[str] = []
    period_ends: list[str] = []
    currency = (raw.get("info") or {}).get("currency")

    for key, artifact_id, url_slug, tool in _STATEMENTS:
        df = raw.get(key)
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        data = statement_to_dict(df)
        meta = StructuredMeta(
            id=artifact_id, ticker=ticker.upper(), producer="fetch",
            title=f"{ticker.upper()} {artifact_id.replace('_yahoo', '').replace('_', ' ')}"
                  f" (annual)",
            source="Yahoo Finance",
            url=f"https://finance.yahoo.com/quote/{ticker.upper()}/{url_slug}",
            provider_tool=tool, fetch_cmd=fetch_cmd(ticker, "financials"),
            fetched_at=now.isoformat(),
            # §6.4: as_of is the period end, not the fetch time.
            as_of=max(data), period="annual",
            # §6.4: reporting currency is recorded; no FX conversion is performed.
            currency=currency)
        paths.append(write_structured(ticker_dir, meta, data))
        written_ids.append(artifact_id)
        period_ends.append(max(data))

    if not written_ids:
        return False, [], f"no financial statements returned for {ticker.upper()}"

    info = raw.get("info") or {}
    # Nulls stay null (§6.4): a ratio Yahoo does not supply is absent, not zero.
    ratios = {cat: {name: json_safe(info.get(key)) for name, key in fields.items()}
              for cat, fields in RATIO_FIELDS.items()}
    ev_warning = _add_computed_ev(ratios)

    derived_from = list(written_ids)
    if (ticker_dir / "structured" / "profile_yahoo.json").exists():
        derived_from.append("profile_yahoo")

    rmeta = StructuredMeta(
        id="key_ratios_computed", ticker=ticker.upper(), producer="compute",
        title=f"{ticker.upper()} key ratios (TTM)",
        source=SOURCE_COMPUTED,
        provider_tool="lib/fetchers/fundamentals.py",
        fetch_cmd=fetch_cmd(ticker, "financials"),
        computed_at=now.isoformat(),
        # §6.4: TTM is used only as supplied by the provider — never built here
        # from four quarters.
        period="ttm", as_of=max(period_ends), currency=currency,
        derived_from=derived_from)
    paths.append(write_structured(ticker_dir, rmeta, ratios))

    # All four ids: §7's own example lists them together, and recording only one
    # would leave a deleted statement invisible to the missing-artifact check.
    record_fetch(state, "financials", written_ids + ["key_ratios_computed"], now,
                 {"policy": "on_earnings"})
    return True, paths, ev_warning
