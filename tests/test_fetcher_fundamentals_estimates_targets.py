"""Tests for the fundamentals, estimates and targets fetchers (§6.2, §6.3, §6.4)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from lib.fetchers.estimates import fetch_estimates, revision_deltas
from lib.fetchers.fundamentals import fetch_financials
from lib.fetchers.targets import fetch_targets
from lib.provenance import read_structured
from lib.statefile import init_state
from lib.validate import validate

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _statement() -> pd.DataFrame:
    return pd.DataFrame(
        {pd.Timestamp("2025-07-31"): [8027000000.0, np.nan],
         pd.Timestamp("2024-07-31"): [6893000000.0, 440000000.0]},
        index=["TotalRevenue", "NetIncome"])


FAKE_FINANCIALS = {
    "income_stmt": _statement(),
    "balance_sheet": _statement(),
    "cashflow": _statement(),
    "info": {"trailingPE": 55.2, "marketCap": 105_000_000_000, "currency": "USD",
             "grossMargins": 0.74, "pegRatio": None},
}


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


def _meta(ticker_dir: Path, artifact_id: str):
    return read_structured(ticker_dir / "structured" / f"{artifact_id}.json")[0]


# --- fundamentals: statements --------------------------------------------

def test_each_statement_is_its_own_artifact(tmp_ticker_dir: Path):
    """§6.3 source separation: a citation resolves to the one Yahoo page that
    shows that statement."""
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_financials("PANW", tmp_ticker_dir, state,
                                       financials_provider=lambda t: FAKE_FINANCIALS,
                                       now=NOW)
    assert ok and err is None
    for artifact_id, slug in (("income_statement_yahoo", "financials"),
                              ("balance_sheet_yahoo", "balance-sheet"),
                              ("cashflow_yahoo", "cash-flow")):
        assert _meta(tmp_ticker_dir, artifact_id).url.endswith(slug)


def test_statement_as_of_is_the_period_end(tmp_ticker_dir: Path):
    """§6.4: as_of is the period end, not the fetch date."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    assert _meta(tmp_ticker_dir, "income_statement_yahoo").as_of == "2025-07-31"


def test_statements_record_the_reporting_currency(tmp_ticker_dir: Path):
    """§6.4: currency is recorded and no FX conversion is performed, so a
    non-USD reporter can be disclosed rather than silently mixed."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    assert _meta(tmp_ticker_dir, "income_statement_yahoo").currency == "USD"


def test_statement_period_is_annual(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    assert _meta(tmp_ticker_dir, "income_statement_yahoo").period == "annual"


def test_nulls_stay_null(tmp_ticker_dir: Path):
    """§6.4: missing values are never zero-filled."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    _m, data = read_structured(
        tmp_ticker_dir / "structured" / "income_statement_yahoo.json")
    assert data["2025-07-31"]["NetIncome"] is None


# --- fundamentals: key ratios --------------------------------------------

def test_key_ratios_carry_the_compute_shape(tmp_ticker_dir: Path):
    """§6.2: `compute` requires computed_at and a non-empty derived_from, and
    forbids url."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    meta = _meta(tmp_ticker_dir, "key_ratios_computed")
    assert meta.producer == "compute"
    assert meta.computed_at == NOW.isoformat()
    assert meta.url is None
    assert set(meta.derived_from) >= {"income_statement_yahoo", "balance_sheet_yahoo",
                                      "cashflow_yahoo"}


def test_key_ratios_period_is_ttm(tmp_ticker_dir: Path):
    """§6.4 admits quarterly | annual | ttm; TTM is used only as the provider
    supplies it, never built here from four quarters."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    assert _meta(tmp_ticker_dir, "key_ratios_computed").period == "ttm"


def test_key_ratios_null_stays_null(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    _m, data = read_structured(
        tmp_ticker_dir / "structured" / "key_ratios_computed.json")
    assert data["valuation"]["peg_ratio"] is None
    assert data["valuation"]["trailing_pe"] == 55.2


def test_derived_from_names_only_statements_actually_written(tmp_ticker_dir: Path):
    """Every derived_from id must resolve (§8.4 check 5), so a statement the
    provider omitted must not be claimed as an input."""
    state = init_state(tmp_ticker_dir, "PANW")
    partial = {**FAKE_FINANCIALS, "cashflow": pd.DataFrame()}
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: partial, now=NOW)
    assert "cashflow_yahoo" not in _meta(tmp_ticker_dir, "key_ratios_computed").derived_from
    assert _errors(tmp_ticker_dir) == []


def test_all_statements_missing_is_a_failure(tmp_ticker_dir: Path):
    """Otherwise the ratios artifact would be stamped with an empty lineage,
    which §6.2 forbids anyway."""
    state = init_state(tmp_ticker_dir, "PANW")
    empty = {"income_stmt": pd.DataFrame(), "balance_sheet": pd.DataFrame(),
             "cashflow": pd.DataFrame(), "info": {}}
    ok, _paths, err = fetch_financials("PANW", tmp_ticker_dir, state,
                                       financials_provider=lambda t: empty, now=NOW)
    assert not ok and "no financial statements" in err


def test_financials_records_all_four_ids(tmp_ticker_dir: Path):
    """§7's own example lists all four together; recording one would leave a
    deleted statement invisible to the missing-artifact check (§10.1)."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    assert state["data"]["financials"]["current_ids"] == [
        "income_statement_yahoo", "balance_sheet_yahoo", "cashflow_yahoo",
        "key_ratios_computed"]
    assert state["data"]["financials"]["policy"] == "on_earnings"


def test_fundamentals_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_financials("PANW", tmp_ticker_dir, state,
                     financials_provider=lambda t: FAKE_FINANCIALS, now=NOW)
    assert _errors(tmp_ticker_dir) == []


def test_fundamentals_provider_error_is_returned(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")

    def boom(t):
        raise ConnectionError("network down")

    ok, _paths, err = fetch_financials("PANW", tmp_ticker_dir, state,
                                       financials_provider=boom, now=NOW)
    assert not ok and "network down" in err


# --- estimates ------------------------------------------------------------

FAKE_ESTIMATES = {
    "earnings_estimate": pd.DataFrame({"avg": [1.2, 5.4]}, index=["0q", "+1y"]),
    "revenue_estimate": pd.DataFrame({"avg": [2.4e9]}, index=["0q"]),
    "eps_revisions": pd.DataFrame({"upLast30days": [7]}, index=["0q"]),
    "eps_trend": pd.DataFrame({"current": [1.2], "90daysAgo": [1.0]}, index=["0q"]),
}


def test_estimates_writes_two_artifacts(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, paths, err = fetch_estimates("PANW", tmp_ticker_dir, state,
                                     estimates_provider=lambda t: FAKE_ESTIMATES,
                                     now=NOW)
    assert ok and err is None and len(paths) == 2
    assert _meta(tmp_ticker_dir, "estimates_yahoo").producer == "fetch"
    assert _meta(tmp_ticker_dir, "eps_revisions_yahoo").producer == "fetch"


def test_estimates_records_both_ids(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_estimates("PANW", tmp_ticker_dir, state,
                    estimates_provider=lambda t: FAKE_ESTIMATES, now=NOW)
    assert state["data"]["estimates"]["current_ids"] == [
        "estimates_yahoo", "eps_revisions_yahoo"]


def test_revision_deltas_quantify_the_move():
    """The point of eps_trend: a report needs "consensus revised up 20% over
    90 days", not five undifferentiated numbers."""
    out = revision_deltas({"current_quarter": {"current": 1.2, "90daysAgo": 1.0}})
    assert out["current_quarter"]["revision_pct_90d"] == 20.0


def test_revision_deltas_skip_a_zero_prior():
    """Dividing by a zero prior would be a crash or an infinity, neither of
    which is a revision."""
    out = revision_deltas({"q": {"current": 1.2, "90daysAgo": 0}})
    assert "revision_pct_90d" not in out["q"]


def test_estimates_empty_result_fails(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_estimates("PANW", tmp_ticker_dir, state,
                                      estimates_provider=lambda t: {}, now=NOW)
    assert not ok and "no analyst estimate data" in err


def test_estimates_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_estimates("PANW", tmp_ticker_dir, state,
                    estimates_provider=lambda t: FAKE_ESTIMATES, now=NOW)
    assert _errors(tmp_ticker_dir) == []


# --- targets --------------------------------------------------------------

FAKE_TARGETS = {
    "price_targets": {"current": 200.0, "mean": 240.0, "high": 300.0, "low": 180.0},
    "upgrades_downgrades": pd.DataFrame(
        {"Firm": ["Big Bank"], "ToGrade": ["Buy"]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-01")])),
    "recommendations": pd.DataFrame({"period": ["0m"], "strongBuy": [12]}),
}


def test_targets_computes_implied_upside(tmp_ticker_dir: Path):
    """The number a verdict card actually needs."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_targets("PANW", tmp_ticker_dir, state,
                  targets_provider=lambda t: FAKE_TARGETS, now=NOW)
    _m, data = read_structured(
        tmp_ticker_dir / "structured" / "price_targets_yahoo.json")
    assert data["price_targets"]["upside_pct_mean"] == 20.0


def test_targets_keeps_recent_rating_actions(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_targets("PANW", tmp_ticker_dir, state,
                  targets_provider=lambda t: FAKE_TARGETS, now=NOW)
    _m, data = read_structured(
        tmp_ticker_dir / "structured" / "price_targets_yahoo.json")
    assert data["recent_actions"][0]["Firm"] == "Big Bank"


def test_targets_grid_drops_a_meaningless_range_index(tmp_ticker_dir: Path):
    """yfinance returns the grid with `period` as a column and a RangeIndex;
    resetting it would inject an "index": 0,1,2 key into every stored row."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_targets("PANW", tmp_ticker_dir, state,
                  targets_provider=lambda t: FAKE_TARGETS, now=NOW)
    _m, data = read_structured(
        tmp_ticker_dir / "structured" / "recommendations_yahoo.json")
    assert "index" not in data["grid"][0]
    assert data["grid"][0]["period"] == "0m"


def test_targets_empty_result_fails(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_targets("PANW", tmp_ticker_dir, state,
                                    targets_provider=lambda t: {}, now=NOW)
    assert not ok and "no price target data" in err


def test_targets_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_targets("PANW", tmp_ticker_dir, state,
                  targets_provider=lambda t: FAKE_TARGETS, now=NOW)
    assert _errors(tmp_ticker_dir) == []


# --- provider enterprise value vs the snapshot's own inputs --------------------

def test_computed_ev_is_added_from_same_provider_inputs() -> None:
    """§6.4 forbids CROSS-provider arithmetic; market cap, debt and cash all come
    from one `info` snapshot, so reconciling them here is same-provider."""
    from lib.fetchers.fundamentals import _add_computed_ev

    ratios = {"highlights": {"market_cap": 100.0, "enterprise_value": 90.0,
                             "total_cash": 10.0, "total_debt": 0.0,
                             "revenue_ttm": 50.0, "ebitda": 10.0},
              "valuation": {}}

    assert _add_computed_ev(ratios) is None          # consistent: no warning
    assert ratios["highlights"]["enterprise_value_computed"] == 90.0
    assert ratios["valuation"]["ev_to_revenue_computed"] == 1.8
    assert ratios["valuation"]["ev_to_ebitda_computed"] == 9.0


def test_diverging_provider_ev_warns_and_keeps_both_figures() -> None:
    """TOST's real 2026-08-12 snapshot. The provider's EV implies $840M of net
    cash against its own $1,713M — every EV multiple downstream inherited it,
    and a synthesizer three stages later was what noticed."""
    from lib.fetchers.fundamentals import _add_computed_ev

    ratios = {"highlights": {"market_cap": 19_287.9e6, "enterprise_value": 18_447.6e6,
                             "total_cash": 1_713.0e6, "total_debt": 0.0,
                             "revenue_ttm": 6_800e6, "ebitda": 487e6},
              "valuation": {}}

    warning = _add_computed_ev(ratios)

    assert warning is not None and "enterprise_value" in warning
    assert "+5.0%" in warning
    # The provider's figure is NOT overwritten — the artifact stays a pass-through.
    assert ratios["highlights"]["enterprise_value"] == 18_447.6e6
    assert ratios["highlights"]["enterprise_value_computed"] == 17_574.9e6


def test_computed_ev_is_skipped_when_an_input_is_missing() -> None:
    """Nulls stay null (§6.4): an absent input yields no computed figure and no
    warning, rather than a zero that reads as a measurement."""
    from lib.fetchers.fundamentals import _add_computed_ev

    ratios = {"highlights": {"market_cap": None, "total_cash": 5.0,
                             "total_debt": 0.0}, "valuation": {}}

    assert _add_computed_ev(ratios) is None
    assert "enterprise_value_computed" not in ratios["highlights"]


# --- per-attribute target warnings --------------------------------------------

def test_targets_full_payload_warns_about_nothing(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, warning = fetch_targets(
        "PANW", tmp_ticker_dir, state,
        targets_provider=lambda t: FAKE_TARGETS, now=NOW)

    assert (ok, warning) == (True, None)


def test_empty_rating_actions_warn_while_the_fetch_still_succeeds(tmp_ticker_dir: Path):
    """TOST, 2026-08-12. Only the all-three-empty guard used to fire, so an empty
    `upgrades_downgrades` beside populated targets persisted as a clean success —
    and the artifact's stale $25 low reached a published report as current."""
    state = init_state(tmp_ticker_dir, "PANW")
    thin = dict(FAKE_TARGETS, upgrades_downgrades=pd.DataFrame())

    ok, paths, warning = fetch_targets("PANW", tmp_ticker_dir, state,
                                       targets_provider=lambda t: thin, now=NOW)

    assert ok is True                      # a partial payload is a §22.3 degradation
    assert paths                           # and the artifacts still write
    assert warning is not None
    assert "upgrades_downgrades empty" in warning
    assert "may be stale" in warning
    assert "price_targets empty" not in warning     # targets were fine — say so
