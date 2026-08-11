"""Tests for the profile, prices and calendar fetchers (spec §6, §6.4, §11.1)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from lib.fetchers.calendar import (
    earnings_dates_to_dict, fetch_calendar, last_earnings_date, next_earnings_date,
)
from lib.fetchers.prices import fetch_prices
from lib.fetchers.profile import fetch_profile
from lib.provenance import read_structured
from lib.statefile import init_state
from lib.validate import validate

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

FAKE_INFO = {
    "longName": "Palo Alto Networks, Inc.",
    "sector": "Technology",
    "industry": "Software - Infrastructure",
    "longBusinessSummary": "Palo Alto Networks provides cybersecurity platforms.",
    "website": "https://www.paloaltonetworks.com",
    "country": "United States",
    "fullTimeEmployees": 15000,
    "marketCap": 105_000_000_000,
    "currency": "USD",
    "irrelevantKey": "dropped",
}


def _price_frame(n: int = 3) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-28") + pd.Timedelta(days=i)
                            for i in range(n)])
    return pd.DataFrame(
        {"Open": [1.0] * n, "High": [2.0] * n, "Low": [0.5] * n,
         "Close": [1.5] * n, "Volume": [100] * n}, index=idx)


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- profile --------------------------------------------------------------

def test_profile_writes_provenance(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, paths, err = fetch_profile("PANW", tmp_ticker_dir, state,
                                   info_provider=lambda t: FAKE_INFO, now=NOW)
    assert ok and err is None
    meta, data = read_structured(tmp_ticker_dir / "structured" / "profile_yahoo.json")
    assert meta.producer == "fetch"
    assert meta.source == "Yahoo Finance"
    assert meta.url == "https://finance.yahoo.com/quote/PANW/profile"
    assert meta.fetched_at == NOW.isoformat()
    assert data["longName"] == "Palo Alto Networks, Inc."
    assert "irrelevantKey" not in data


def test_profile_carries_a_fetch_cmd_and_title(tmp_ticker_dir: Path):
    """§6/§8.4 check 2: every bronze artifact carries fetch_cmd."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_profile("PANW", tmp_ticker_dir, state,
                  info_provider=lambda t: FAKE_INFO, now=NOW)
    meta, _ = read_structured(tmp_ticker_dir / "structured" / "profile_yahoo.json")
    assert meta.fetch_cmd == "uv run python sra.py prefetch PANW --kinds profile"
    assert meta.title


def test_profile_sets_company_name_and_state(tmp_ticker_dir: Path):
    """wikipedia searches on company_name in the next dependency wave."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_profile("PANW", tmp_ticker_dir, state,
                  info_provider=lambda t: FAKE_INFO, now=NOW)
    assert state["company_name"] == "Palo Alto Networks, Inc."
    assert state["data"]["profile"]["current_ids"] == ["profile_yahoo"]
    assert state["data"]["profile"]["policy_days"] == 90


def test_profile_provider_error_is_returned_not_raised(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")

    def boom(t: str) -> dict:
        raise ConnectionError("network down")

    ok, _paths, err = fetch_profile("PANW", tmp_ticker_dir, state,
                                    info_provider=boom, now=NOW)
    assert not ok and "network down" in err
    assert "profile" not in state["data"]


@pytest.mark.parametrize("bogus", [{}, {"symbol": "NOTATICKER"}, {"longName": ""}])
def test_profile_unresolved_ticker_fails(tmp_ticker_dir: Path, bogus: dict):
    """A response resolving to no company must fail, not persist an all-null
    profile under a 90-day stamp that would suppress retries for three months."""
    state = init_state(tmp_ticker_dir, "NOTATICKER")
    ok, _paths, err = fetch_profile("NOTATICKER", tmp_ticker_dir, state,
                                    info_provider=lambda t: bogus, now=NOW)
    assert not ok and "invalid ticker" in err
    assert not (tmp_ticker_dir / "structured" / "profile_yahoo.json").exists()


def test_profile_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_profile("PANW", tmp_ticker_dir, state,
                  info_provider=lambda t: FAKE_INFO, now=NOW)
    assert _errors(tmp_ticker_dir) == []


# --- prices ---------------------------------------------------------------

def test_prices_writes_provenance(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_prices("PANW", tmp_ticker_dir, state,
                                   prices_provider=lambda s: _price_frame(), now=NOW)
    assert ok and err is None
    meta, data = read_structured(tmp_ticker_dir / "structured" / "prices_yahoo.json")
    assert meta.producer == "fetch"
    assert meta.fetch_cmd == "uv run python sra.py prefetch PANW --kinds prices"
    assert data["daily"]["close"] == [1.5, 1.5, 1.5]


def test_prices_as_of_is_the_last_bar_not_the_fetch_time(tmp_ticker_dir: Path):
    """§6.4: as_of is the period end."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_prices("PANW", tmp_ticker_dir, state,
                 prices_provider=lambda s: _price_frame(), now=NOW)
    meta, _ = read_structured(tmp_ticker_dir / "structured" / "prices_yahoo.json")
    assert meta.as_of == "2026-07-30"


def test_prices_records_the_adjustment_convention(tmp_ticker_dir: Path):
    """§6.4: `_meta.adjusted` records whether prices are split/dividend
    adjusted, so per-share figures cannot silently mix conventions."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_prices("PANW", tmp_ticker_dir, state,
                 prices_provider=lambda s: _price_frame(), now=NOW)
    meta, _ = read_structured(tmp_ticker_dir / "structured" / "prices_yahoo.json")
    assert meta.adjusted is True


def test_prices_does_not_put_a_bar_interval_in_period(tmp_ticker_dir: Path):
    """§6.4 enumerates period as quarterly|annual|ttm — statement periods. A
    price series has a bar interval, which is not one of those."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_prices("PANW", tmp_ticker_dir, state,
                 prices_provider=lambda s: _price_frame(), now=NOW)
    meta, _ = read_structured(tmp_ticker_dir / "structured" / "prices_yahoo.json")
    assert meta.period in (None, "quarterly", "annual", "ttm")


def test_prices_empty_frame_fails(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_prices("PANW", tmp_ticker_dir, state,
                                   prices_provider=lambda s: pd.DataFrame(), now=NOW)
    assert not ok and "no price data" in err


def test_prices_malformed_frame_fails_cleanly(tmp_ticker_dir: Path):
    """A provider shape change must be the fetcher's error contract, not a
    traceback out of a prefetch run."""
    state = init_state(tmp_ticker_dir, "PANW")
    bad = _price_frame().drop(columns=["Volume"])
    ok, _paths, err = fetch_prices("PANW", tmp_ticker_dir, state,
                                   prices_provider=lambda s: bad, now=NOW)
    assert not ok and "Volume" in err


def test_a_benchmark_failure_is_tolerated(tmp_ticker_dir: Path):
    """The relative-strength panel is optional; the ticker series is not."""
    state = init_state(tmp_ticker_dir, "PANW")

    def provider(symbol: str):
        if symbol == "^GSPC":
            raise ConnectionError("benchmark down")
        return _price_frame()

    ok, _paths, err = fetch_prices("PANW", tmp_ticker_dir, state,
                                   prices_provider=provider, now=NOW)
    assert ok and err is None
    _meta, data = read_structured(tmp_ticker_dir / "structured" / "prices_yahoo.json")
    assert data["benchmark"] is None


def test_prices_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_prices("PANW", tmp_ticker_dir, state,
                 prices_provider=lambda s: _price_frame(), now=NOW)
    assert _errors(tmp_ticker_dir) == []


# --- calendar -------------------------------------------------------------

FAKE_CALENDAR = {
    "calendar": {"Earnings Date": ["2026-08-20"], "Dividend Date": "2026-09-01"},
    "earnings_dates": {"2026-05-21": {"Reported EPS": 1.2},
                       "2026-02-19": {"Reported EPS": 1.1},
                       "2026-08-20": {"Reported EPS": None}},
}


def test_calendar_writes_provenance(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_calendar("PANW", tmp_ticker_dir, state,
                                     calendar_provider=lambda t: FAKE_CALENDAR, now=NOW)
    assert ok and err is None
    meta, data = read_structured(
        tmp_ticker_dir / "structured" / "events_calendar_yahoo.json")
    assert meta.producer == "fetch"
    assert meta.fetch_cmd == "uv run python sra.py prefetch PANW --kinds calendar"
    assert "2026-05-21" in data["earnings_dates"]


def test_calendar_tolerates_a_missing_half(tmp_ticker_dir: Path):
    """Yahoo drops one or the other for some tickers; only a completely empty
    result is an error."""
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, _err = fetch_calendar(
        "PANW", tmp_ticker_dir, state,
        calendar_provider=lambda t: {"calendar": {}, "earnings_dates":
                                     {"2026-05-21": {"Reported EPS": 1.2}}}, now=NOW)
    assert ok


def test_calendar_empty_result_fails(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_calendar("PANW", tmp_ticker_dir, state,
                                     calendar_provider=lambda t: {}, now=NOW)
    assert not ok and "no calendar data" in err


def test_calendar_malformed_half_degrades_rather_than_crashing(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, _err = fetch_calendar(
        "PANW", tmp_ticker_dir, state,
        calendar_provider=lambda t: {"calendar": ["not", "a", "mapping"],
                                     "earnings_dates": {"2026-05-21": {}}}, now=NOW)
    assert ok


def test_last_earnings_date_is_the_most_recent_past_event(tmp_ticker_dir: Path):
    """§7: the on_earnings policy uses the most recent PAST event, not the next
    scheduled one — a forward date goes silent the moment it rolls over."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_calendar("PANW", tmp_ticker_dir, state,
                   calendar_provider=lambda t: FAKE_CALENDAR, now=NOW)
    from lib.fetchers.calendar import _last_earnings_date, _read_calendar_data
    assert _last_earnings_date(_read_calendar_data(tmp_ticker_dir),
                               date(2026, 7, 30)) == date(2026, 5, 21)


def test_next_earnings_date_prefers_the_forward_calendar(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_calendar("PANW", tmp_ticker_dir, state,
                   calendar_provider=lambda t: FAKE_CALENDAR, now=NOW)
    from lib.fetchers.calendar import _next_earnings_date, _read_calendar_data
    assert _next_earnings_date(_read_calendar_data(tmp_ticker_dir),
                               date(2026, 7, 30)) == date(2026, 8, 20)


def test_earnings_helpers_return_none_without_an_artifact(tmp_ticker_dir: Path):
    """`status` calls these before any calendar exists; they must not raise."""
    assert last_earnings_date(tmp_ticker_dir) is None
    assert next_earnings_date(tmp_ticker_dir) is None


def test_earnings_dates_to_dict_handles_none():
    assert earnings_dates_to_dict(None) == {}


def test_calendar_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_calendar("PANW", tmp_ticker_dir, state,
                   calendar_provider=lambda t: FAKE_CALENDAR, now=NOW)
    assert _errors(tmp_ticker_dir) == []
