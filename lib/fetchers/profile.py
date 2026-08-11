#!/usr/bin/env python3
"""Profile fetcher: Yahoo Finance company profile with provenance (§11.1).

The pattern exemplar for every fetcher: a provider callable that can be
injected for tests, provider failures returned as `(False, [], msg)` rather
than raised, and one `write_structured` call carrying full §6 provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import fetch_cmd
from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

PROFILE_FIELDS = (
    "longName", "sector", "industry", "longBusinessSummary", "website",
    "country", "fullTimeEmployees", "marketCap", "currency",
)


def _yfinance_info(ticker: str) -> dict:
    import yfinance  # local import: keep the module importable offline

    return yfinance.Ticker(ticker).info


def fetch_profile(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    info_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch a Yahoo Finance company profile and write it with provenance.

    Also sets `state["company_name"]`, which the wikipedia fetcher searches on
    in the next dependency wave (§11.1).
    """
    provider = info_provider or _yfinance_info
    now = now or datetime.now(timezone.utc)
    try:
        info = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"profile fetch failed: {exc}"

    data = {k: info.get(k) for k in PROFILE_FIELDS}
    # An unresolved ticker (typo, delisted, or a provider shape change) comes back
    # as a dict with nothing usable in it. Fail rather than persisting an all-null
    # profile under a 90-day freshness stamp, which would suppress retries for three
    # months and surface no error anywhere.
    if not data.get("longName"):
        return False, [], (
            f"profile fetch failed: no company name for {ticker.upper()} "
            "— likely an invalid ticker"
        )

    meta = StructuredMeta(
        id="profile_yahoo",
        ticker=ticker.upper(),
        producer="fetch",
        title=f"{data['longName']} company profile",
        source="Yahoo Finance",
        url=f"https://finance.yahoo.com/quote/{ticker.upper()}/profile",
        provider_tool="yfinance.Ticker.info",
        fetch_cmd=fetch_cmd(ticker, "profile"),
        fetched_at=now.isoformat(),
        as_of=now.date().isoformat(),
        currency=data.get("currency"),
    )
    path = write_structured(ticker_dir, meta, data)
    record_fetch(state, "profile", "profile_yahoo", now, {"policy_days": 90})
    state["company_name"] = data["longName"]  # guaranteed truthy by the guard above
    return True, [path], None
