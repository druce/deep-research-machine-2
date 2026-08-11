"""Batched FMP profile lookup, threaded revenue lookup, and row enrichment."""
from lib.peers_enrich import enrich, fetch_profiles, fetch_revenues


def test_fetch_profiles_calls_once_per_symbol_never_comma_joined():
    """FMP /stable/profile returns 200 with [] for a comma-separated symbol list.

    Batching therefore yields NO profiles at all, silently, leaving every
    candidate with null classification and an empty description. This test is
    the guard: each call must carry exactly one symbol.
    """
    calls: list[str] = []

    def profile_fn(symbol: str):
        calls.append(symbol)
        return [{"symbol": symbol, "companyName": f"{symbol} Inc.",
                 "sector": "Technology", "industry": "Software - Infrastructure",
                 "country": "US", "exchange": "NASDAQ", "marketCap": 1,
                 "description": "d", "isEtf": False, "isFund": False,
                 "isActivelyTrading": True}]

    symbols = [f"S{i:03d}" for i in range(23)]
    got = fetch_profiles(symbols, profile_fn=profile_fn)
    assert len(calls) == 23
    assert all("," not in c for c in calls)
    assert sorted(calls) == sorted(symbols)
    assert len(got) == 23
    assert got["S000"]["sector"] == "Technology"


def test_fetch_profiles_survives_one_failing_symbol():
    def profile_fn(symbol: str):
        if symbol == "BAD":
            raise RuntimeError("profile 500")
        return [{"symbol": symbol, "sector": "Technology"}]

    got = fetch_profiles(["BAD", "GOOD"], profile_fn=profile_fn)
    assert "BAD" not in got
    assert got["GOOD"]["sector"] == "Technology"


def test_fetch_profiles_skips_a_symbol_that_returns_an_empty_list():
    """The exact live failure shape: HTTP 200 with []."""
    got = fetch_profiles(["GHOST"], profile_fn=lambda s: [])
    assert got == {}


def test_fetch_profiles_returns_empty_for_no_symbols():
    assert fetch_profiles([], profile_fn=lambda s: []) == {}


def test_fetch_revenues_sums_four_quarters_into_a_true_ttm():
    """Real CRWD quarters. The FY figure for the same date is 4,812,005,000 --
    5.9% lower, and on a different window than a peer with another fiscal year."""
    def income_fn(symbol: str):
        return [{"symbol": symbol, "date": "2026-04-30", "period": "Q1",
                 "revenue": 1_385_629_000},
                {"symbol": symbol, "date": "2026-01-31", "period": "Q4",
                 "revenue": 1_305_375_000},
                {"symbol": symbol, "date": "2025-10-31", "period": "Q3",
                 "revenue": 1_234_244_000},
                {"symbol": symbol, "date": "2025-07-31", "period": "Q2",
                 "revenue": 1_168_952_000}]

    assert fetch_revenues(["CRWD"], income_fn=income_fn) == {"CRWD": 5_094_200_000}


def test_fetch_revenues_yields_none_on_fewer_than_four_quarters():
    """A recent IPO with 3 quarters must not report a 25%-low 'TTM'."""
    def income_fn(symbol: str):
        return [{"symbol": symbol, "revenue": 100.0} for _ in range(3)]

    assert fetch_revenues(["NEWCO"], income_fn=income_fn) == {"NEWCO": None}


def test_fetch_revenues_yields_none_when_a_quarter_has_null_revenue():
    def income_fn(symbol: str):
        return [{"symbol": symbol, "revenue": 100.0},
                {"symbol": symbol, "revenue": None},
                {"symbol": symbol, "revenue": 100.0},
                {"symbol": symbol, "revenue": 100.0}]

    assert fetch_revenues(["GAPPY"], income_fn=income_fn) == {"GAPPY": None}


def test_fetch_revenues_uses_only_the_four_most_recent_quarters():
    def income_fn(symbol: str):
        return [{"symbol": symbol, "revenue": 100.0} for _ in range(8)]

    assert fetch_revenues(["OLD"], income_fn=income_fn) == {"OLD": 400.0}


def test_fetch_revenues_yields_none_on_failure_not_an_error():
    def income_fn(symbol: str):
        raise RuntimeError("income 429")

    assert fetch_revenues(["PANW"], income_fn=income_fn) == {"PANW": None}


def test_fetch_revenues_yields_none_on_empty_payload():
    assert fetch_revenues(["PANW"], income_fn=lambda s: []) == {"PANW": None}


# --- "Never raises" is a contract, and the parsing used to sit outside the try.
# Each of these raised out of fetch_peers, which does not wrap its enrichment
# block, so `sra.py peers-candidates` tracebacked instead of returning an exit
# code (CLAUDE.md: main() returns an exit code).

def test_fetch_revenues_never_raises_on_a_non_numeric_revenue():
    def income_fn(symbol: str):
        return [{"revenue": "not a number"}] * 4

    assert fetch_revenues(["PANW"], income_fn=income_fn) == {"PANW": None}


def test_fetch_revenues_coerces_a_stringified_revenue():
    def income_fn(symbol: str):
        return [{"revenue": "100.5"}] * 4

    assert fetch_revenues(["PANW"], income_fn=income_fn) == {"PANW": 402.0}


def test_fetch_revenues_never_raises_on_a_payload_of_non_dicts():
    assert fetch_revenues(["PANW"], income_fn=lambda s: ["oops"] * 4) == {"PANW": None}


def test_fetch_revenues_never_raises_on_a_non_list_payload():
    assert fetch_revenues(["PANW"],
                          income_fn=lambda s: {"Error Message": "x"}) == {"PANW": None}


def test_fetch_profiles_reports_the_symbols_whose_lookup_raised():
    """N1: degrading a bad lookup to "unknown" is right per symbol and blinding
    in bulk -- an expired key nulls every column the rater judges on. The sink
    is what lets fetch_peers tell that from "these symbols have no profile"."""
    def profile_fn(symbol: str):
        if symbol == "BAD":
            raise RuntimeError("FMP profile -> HTTP 401")
        return []                       # answered, but with nothing: not a failure

    failed: list[str] = []
    assert fetch_profiles(["BAD", "GHOST"], profile_fn=profile_fn,
                          failed=failed) == {}
    assert failed == ["BAD"]


def test_fetch_revenues_reports_the_symbols_whose_lookup_raised():
    def income_fn(symbol: str):
        if symbol == "BAD":
            raise RuntimeError("FMP income-statement -> HTTP 429")
        return []

    failed: list[str] = []
    got = fetch_revenues(["BAD", "THIN"], income_fn=income_fn, failed=failed)
    assert got == {"BAD": None, "THIN": None}
    assert failed == ["BAD"]


def test_fetch_profiles_never_raises_on_a_first_element_that_is_not_a_dict():
    assert fetch_profiles(["PANW"], profile_fn=lambda s: ["oops"]) == {}


def test_fetch_profiles_never_raises_on_a_non_list_payload():
    assert fetch_profiles(["PANW"],
                          profile_fn=lambda s: {"Error Message": "x"}) == {}


def test_enrich_attaches_profile_and_revenue_columns():
    rows = [{"symbol": "PANW", "name": "", "fund_count": 3,
             "sources": ["fmp_peers"], "is_subject": False}]
    profiles = {"PANW": {"companyName": "Palo Alto Networks, Inc.",
                         "sector": "Technology",
                         "industry": "Software - Infrastructure",
                         "country": "US", "exchange": "NASDAQ",
                         "marketCap": 140_230_000_000,
                         "description": "Palo Alto Networks provides...",
                         "isEtf": False, "isFund": False,
                         "isActivelyTrading": True}}
    got = enrich(rows, profiles, {"PANW": 9_220_000_000})[0]
    assert got["name"] == "Palo Alto Networks, Inc."
    assert got["fmp_sector"] == "Technology"
    assert got["fmp_industry"] == "Software - Infrastructure"
    assert got["country"] == "US"
    assert got["market_cap"] == 140_230_000_000
    assert got["revenue_ttm"] == 9_220_000_000
    assert got["description"].startswith("Palo Alto Networks")
    assert got["is_etf"] is False and got["is_actively_trading"] is True
    assert "gics" not in " ".join(got.keys()).lower()   # never claim GICS


def test_enrich_keeps_rows_with_no_profile_and_nulls_their_columns():
    rows = [{"symbol": "GHOST", "name": "", "fund_count": 2,
             "sources": ["funds"], "is_subject": False}]
    got = enrich(rows, {}, {})[0]
    assert got["symbol"] == "GHOST"
    assert got["fmp_sector"] is None and got["market_cap"] is None
    assert got["revenue_ttm"] is None
    assert got["is_etf"] is None          # unknown, so hygiene_filter keeps it


def test_enrich_preserves_the_proxy_supplied_name_when_profile_has_none():
    rows = [{"symbol": "APP", "name": "AppLovin Corporation", "fund_count": 0,
             "sources": ["proxy"], "is_subject": False}]
    assert enrich(rows, {}, {})[0]["name"] == "AppLovin Corporation"
