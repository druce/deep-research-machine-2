"""Fund selection for peers source 2: exposure prefilter, band, holdings union."""
import json
from pathlib import Path

import httpx
import pytest

from lib.peers_funds import (
    FMP_ETF_INFO_URL,
    MAX_HOLDINGS,
    MIN_HOLDINGS,
    fmp_etf_holdings_count,
    prefilter_exposure,
    select_funds,
    union_holdings,
)

FIXTURE = Path(__file__).parent / "fixtures" / "etf_exposure_crwd.json"

# holdingsCount as returned by FMP /stable/etf/info on 2026-08-01.
# None models a leveraged ETN (FNGU) which reports no holdings count.
HOLDINGS_COUNTS = {
    "VIRS": 56, "SZNE": 7995, "CLOD": 51, "SPAM": 36, "VETS": 6061,
    "VCLO": 31, "WEPN": 107, "FNGU": None, "HAKY": 74, "AINT": 0,
}


def _rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_prefilter_drops_leveraged_foreign_and_null_weight():
    kept = [r["etf_symbol"] for r in prefilter_exposure(_rows())]
    assert "CRWL" not in kept        # weight 2.00 -> leveraged single-stock fund
    assert "CIBR.L" not in kept      # foreign listing (dot)
    assert "WCBR LN" not in kept     # foreign listing (space)
    assert "IWDACL.SN" not in kept   # foreign listing (dot)
    assert "NOWEIGHT" not in kept    # null weight
    assert "SPAM" in kept and "VIRS" in kept


def test_prefilter_sorts_by_weight_descending():
    weights = [r["weight"] for r in prefilter_exposure(_rows())]
    assert weights == sorted(weights, reverse=True)


def test_spec_24_pinned_fund_filter_case():
    """§24's pinned fund-filter case, as one statement over the whole chain.

    The four rejections each exercise a DIFFERENT rule, which is why the block
    names them together: CRWL is dropped by the subject-weight cap (leveraged),
    CIBR.L by the dot rule (foreign listing), and SZNE (7995 holdings) and VETS
    (6061) by the holdings band — the last two only after an /etf/info lookup,
    so they survive the prefilter and die in `select_funds`. A regression in any
    one rule changes this list.
    """
    picked = [f["symbol"] for f in
              select_funds(prefilter_exposure(_rows()), HOLDINGS_COUNTS.get, top_n=5)]
    assert picked == ["VIRS", "CLOD", "SPAM", "VCLO", "WEPN"]
    assert {"CRWL", "SZNE", "VETS", "CIBR.L"}.isdisjoint(picked)


def test_select_funds_applies_holdings_band_and_takes_top_five():
    """SZNE(7995)/VETS(6061)/AINT(0)/FNGU(None) fall outside the band."""
    picked = select_funds(prefilter_exposure(_rows()), HOLDINGS_COUNTS.get, top_n=5)
    assert [f["symbol"] for f in picked] == ["VIRS", "CLOD", "SPAM", "VCLO", "WEPN"]
    assert picked[0]["holdings_count"] == 56
    assert all(MIN_HOLDINGS <= f["holdings_count"] <= MAX_HOLDINGS for f in picked)


def test_select_funds_stops_at_top_n():
    picked = select_funds(prefilter_exposure(_rows()), HOLDINGS_COUNTS.get, top_n=2)
    assert [f["symbol"] for f in picked] == ["VIRS", "CLOD"]


def test_select_funds_skips_funds_whose_info_lookup_fails():
    """An info_fn raising for one symbol must not kill the whole selection."""
    def flaky(symbol: str):
        if symbol == "CLOD":
            raise RuntimeError("info endpoint 500")
        return HOLDINGS_COUNTS.get(symbol)

    picked = select_funds(prefilter_exposure(_rows()), flaky, top_n=3)
    assert [f["symbol"] for f in picked] == ["VIRS", "SPAM", "VCLO"]


def test_select_funds_reports_the_symbols_whose_info_lookup_failed():
    """C2: without this sink, /etf/info down for every fund returns [] -- exactly
    what "no fund fell inside the band" returns, and the source vanishes at
    exit 0 with warnings: null."""
    def dead(symbol: str) -> int:
        raise RuntimeError("FMP info -> HTTP 403")

    failed: list[str] = []
    candidates = prefilter_exposure(_rows())
    assert select_funds(candidates, dead, failed=failed) == []
    assert len(failed) == len(candidates)      # every one attempted, every one failed
    assert "VIRS" in failed


def test_select_funds_sink_stays_empty_when_the_band_simply_excludes_everything():
    """The distinguishing case: the lookups worked, the funds just did not fit."""
    failed: list[str] = []
    assert select_funds(prefilter_exposure(_rows()), lambda s: 9_999,
                        failed=failed) == []
    assert failed == []


def test_fmp_etf_holdings_count_reads_the_first_row(monkeypatch):
    """Offline coverage for the default info_fn: its total failure IS C2."""
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"] = url, params
        return httpx.Response(200, json=[{"symbol": "VIRS", "holdingsCount": 56}])

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    assert fmp_etf_holdings_count("VIRS") == 56
    assert seen["url"] == FMP_ETF_INFO_URL
    assert seen["params"] == {"symbol": "VIRS", "apikey": "test-key"}


def test_fmp_etf_holdings_count_returns_none_when_the_fund_reports_no_count(
        monkeypatch):
    """An ETN that answers but carries no holdingsCount is UNKNOWN, not a failure."""
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json=[{"symbol": "FNGU", "holdingsCount": None}]))
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    assert fmp_etf_holdings_count("FNGU") is None


def test_fmp_etf_holdings_count_raises_on_an_empty_answer(monkeypatch):
    """N5: `200 []` for a symbol the exposure feed just named is the endpoint
    failing to answer -- the /etf/holder 404 failure mode wearing a 200. Read as
    "unknown count" it was reported as a band rejection, hiding an outage."""
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json=[]))
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="no usable row"):
        fmp_etf_holdings_count("VIRS")


def test_fmp_etf_holdings_count_raises_on_a_403_without_the_key(monkeypatch):
    secret = "SUPERSECRETKEY"
    request = httpx.Request("GET", f"{FMP_ETF_INFO_URL}?symbol=VIRS&apikey={secret}")
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: httpx.Response(403, request=request))
    monkeypatch.setenv("FMP_API_KEY", secret)
    with pytest.raises(RuntimeError) as excinfo:
        fmp_etf_holdings_count("VIRS")
    assert secret not in str(excinfo.value)
    assert "403" in str(excinfo.value)


def test_fmp_etf_holdings_count_raises_on_an_error_envelope(monkeypatch):
    """HTTP 200 + a dict is FMP's throttle/entitlement error, not "no data"."""
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json={"Error Message": "Limit Reach"}))
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="expected a list"):
        fmp_etf_holdings_count("VIRS")


def test_union_holdings_counts_funds_per_symbol():
    fund_holdings = {
        "SPAM": [{"symbol": "PANW", "weight": 0.09}, {"symbol": "ZS", "weight": 0.03}],
        "VCLO": [{"symbol": "PANW", "weight": 0.08}, {"symbol": "S", "weight": 0.02}],
        "VIRS": [{"symbol": "MRNA", "weight": 0.05}],
    }
    counts = union_holdings(fund_holdings)
    assert counts == {"PANW": 2, "ZS": 1, "S": 1, "MRNA": 1}


def test_union_holdings_truncates_each_fund_to_top_k_by_weight():
    fund_holdings = {
        "SPAM": [{"symbol": "A", "weight": 0.09},
                 {"symbol": "B", "weight": 0.03},
                 {"symbol": "C", "weight": 0.01}],
    }
    assert union_holdings(fund_holdings, top_k=2) == {"A": 1, "B": 1}


def test_union_holdings_ignores_cash_and_null_weight_rows():
    """FMP holdings carry $USD/$CAD cash lines and rows with no weight."""
    fund_holdings = {
        "SPAM": [{"symbol": "$USD", "weight": 0.004},
                 {"symbol": "$CAD", "weight": 1e-10},
                 {"symbol": "NOWT", "weight": None},
                 {"symbol": "PANW", "weight": 0.09}],
    }
    assert union_holdings(fund_holdings) == {"PANW": 1}


def test_union_holdings_ignores_rows_with_a_null_symbol():
    """`str(None).upper()` is "NONE" -- a fabricated ticker that used to clear
    both filters (2 funds, all hygiene flags unknown) and reach the rater."""
    fund_holdings = {
        "SPAM": [{"symbol": None, "weight": 0.9}, {"symbol": "PANW", "weight": 0.09}],
        "VCLO": [{"symbol": None, "weight": 0.9}, {"symbol": "PANW", "weight": 0.08}],
    }
    assert union_holdings(fund_holdings) == {"PANW": 2}
