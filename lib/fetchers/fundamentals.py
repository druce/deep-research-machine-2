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
    return True, paths, None
