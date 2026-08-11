"""Tests for the macro fetchers and `prefetch-macro` (spec §12).

Macro evidence is shared across tickers and lives in the one `_MACRO` tree.
§12.2 is emphatic that a markup change must fail loudly rather than persist a
misparse, and §12.3 makes a failed macro series a warning rather than a build
failure — these tests pin both halves of that.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sra
from lib.fetchers.fred import (
    FREQUENCY_POLICY_DAYS, UNKNOWN_FREQUENCY_POLICY_DAYS, artifact_id,
    fetch_fred_series, policy_for,
)
from lib.fetchers.multpl import MULTPL_SERIES, ShapeError, fetch_multpl_series, parse_table
from lib.provenance import read_structured
from lib.statefile import init_state
from lib.validate import validate

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"


def _fred_fixture() -> dict:
    return json.loads((FIXTURES / "fred_dgs10.json").read_text(encoding="utf-8"))


def _multpl_fixture() -> str:
    return (FIXTURES / "multpl_sp500_pe.html").read_text(encoding="utf-8")


def _errors(macro_dir: Path):
    return [f for f in validate(macro_dir, macro_dir.parent) if f.severity == "error"]


# --- FRED frequency policy (§12.1) ---------------------------------------

@pytest.mark.parametrize("code,days", list(FREQUENCY_POLICY_DAYS.items()))
def test_frequency_maps_to_its_policy(code: str, days: int):
    """A daily series goes stale in 2 days; an annual one not for over a year,
    so refetching it nightly is pure waste."""
    policy, warning = policy_for(code)
    assert policy == {"policy_days": days}
    assert warning is None


def test_the_policy_table_matches_the_spec():
    assert FREQUENCY_POLICY_DAYS == {"D": 2, "W": 9, "M": 40, "Q": 100, "A": 400}


def test_an_unknown_frequency_defaults_and_warns():
    """§12.1: silently guessing would hide a provider change."""
    policy, warning = policy_for("Bi-Weekly")
    assert policy == {"policy_days": UNKNOWN_FREQUENCY_POLICY_DAYS}
    assert warning and "unknown" in warning.lower()


def test_a_missing_frequency_defaults_and_warns():
    _policy, warning = policy_for(None)
    assert warning is not None


# --- FRED fetch -----------------------------------------------------------

def test_fred_writes_provenance_and_metadata(tmp_macro_dir: Path):
    state = init_state(tmp_macro_dir, "_MACRO")
    ok, _paths, warn = fetch_fred_series("DGS10", tmp_macro_dir, state,
                                         series_provider=lambda s: _fred_fixture(),
                                         now=NOW)
    assert ok and warn is None
    meta, data = read_structured(tmp_macro_dir / "structured" / "fred_dgs10.json")
    assert meta.producer == "fetch"
    assert meta.ticker == "_MACRO"
    # §12.1's required metadata fields
    for field in ("title", "units", "frequency_short", "seasonal_adjustment",
                  "last_updated", "realtime_start", "realtime_end"):
        assert data["series"][field], field
    assert len(data["observations"]) == 3


def test_fred_artifact_id_is_lowercased(tmp_macro_dir: Path):
    assert artifact_id("DGS10") == "fred_dgs10"


def test_fred_as_of_is_the_last_observation(tmp_macro_dir: Path):
    """§6.4: as_of is the period end, not the fetch time."""
    state = init_state(tmp_macro_dir, "_MACRO")
    fetch_fred_series("DGS10", tmp_macro_dir, state,
                      series_provider=lambda s: _fred_fixture(), now=NOW)
    meta, _ = read_structured(tmp_macro_dir / "structured" / "fred_dgs10.json")
    assert meta.as_of == "2026-07-30"


def test_fred_records_the_frequency_policy(tmp_macro_dir: Path):
    """DGS10 is daily, so it ages in 2 days."""
    state = init_state(tmp_macro_dir, "_MACRO")
    fetch_fred_series("DGS10", tmp_macro_dir, state,
                      series_provider=lambda s: _fred_fixture(), now=NOW)
    assert state["data"]["fred_dgs10"]["policy_days"] == 2


def test_fred_never_records_the_api_key(tmp_macro_dir: Path, monkeypatch):
    """§5/§11.1: no raw provider key in any artifact, and the credential
    parameter is OMITTED from `request`, never blanked."""
    monkeypatch.setenv("FRED_API_KEY", "0123456789abcdef0123456789abcdef")
    state = init_state(tmp_macro_dir, "_MACRO")
    fetch_fred_series("DGS10", tmp_macro_dir, state,
                      series_provider=lambda s: _fred_fixture(), now=NOW)
    raw = (tmp_macro_dir / "structured" / "fred_dgs10.json").read_text(encoding="utf-8")
    assert "0123456789abcdef0123456789abcdef" not in raw
    assert "api_key" not in raw
    assert _errors(tmp_macro_dir) == []


def test_fred_no_observations_is_a_failure(tmp_macro_dir: Path):
    state = init_state(tmp_macro_dir, "_MACRO")
    ok, _paths, err = fetch_fred_series(
        "DGS10", tmp_macro_dir, state,
        series_provider=lambda s: {"series": {}, "observations": []}, now=NOW)
    assert not ok and "no observations" in err


def test_fred_provider_error_is_returned(tmp_macro_dir: Path):
    state = init_state(tmp_macro_dir, "_MACRO")

    def boom(s):
        raise ConnectionError("network down")

    ok, _paths, err = fetch_fred_series("DGS10", tmp_macro_dir, state,
                                        series_provider=boom, now=NOW)
    assert not ok and "network down" in err


def test_an_unknown_frequency_still_succeeds_with_a_warning(tmp_macro_dir: Path):
    state = init_state(tmp_macro_dir, "_MACRO")
    payload = _fred_fixture()
    payload["series"]["frequency_short"] = "Fortnightly"
    ok, _paths, warn = fetch_fred_series("DGS10", tmp_macro_dir, state,
                                         series_provider=lambda s: payload, now=NOW)
    assert ok and warn
    assert state["data"]["fred_dgs10"]["policy_days"] == UNKNOWN_FREQUENCY_POLICY_DAYS


# --- multpl shape validation (§12.2) -------------------------------------

def test_multpl_parses_a_well_formed_table():
    rows = parse_table(_multpl_fixture(), (1.0, 200.0))
    assert rows[0] == {"date": "2026-07-30", "value": 28.41}
    assert len(rows) == 3


@pytest.mark.parametrize("html,reason", [
    ("<html><body><p>no table here</p></body></html>", "no HTML table"),
    ("<table><tr><td>Jul 30, 2026</td></tr></table>", "2 columns"),
    ("<table><tr><th>a</th><th>b</th></tr>"
     "<tr><td>not a date</td><td>28.41</td></tr></table>", "dates"),
    ("<table><tr><th>a</th><th>b</th></tr>"
     "<tr><td>Jul 30, 2026</td><td>not a number</td></tr></table>", "values"),
])
def test_markup_changes_fail_loudly(html: str, reason: str):
    """§12.2: "Markup changes must fail loudly." A silently misparsed CAPE
    would propagate into a valuation section as though it were evidence."""
    with pytest.raises(ShapeError) as exc:
        parse_table(html, (1.0, 200.0))
    assert reason in str(exc.value)


def test_an_implausible_value_fails_the_shape_check():
    """The range is a sanity bound, not a forecast: it catches a markup change
    that turns a percentage into an index level."""
    html = ("<table><tr><th>a</th><th>b</th></tr>"
            "<tr><td>Jul 30, 2026</td><td>99999</td></tr></table>")
    with pytest.raises(ShapeError) as exc:
        parse_table(html, (1.0, 200.0))
    assert "plausible range" in str(exc.value)


def test_a_non_monotonic_date_column_fails():
    html = ("<table><tr><th>a</th><th>b</th></tr>"
            "<tr><td>Jul 30, 2026</td><td>28.4</td></tr>"
            "<tr><td>Sep 30, 2026</td><td>27.9</td></tr>"
            "<tr><td>Jun 30, 2026</td><td>27.1</td></tr></table>")
    with pytest.raises(ShapeError) as exc:
        parse_table(html, (1.0, 200.0))
    assert "monotonic" in str(exc.value)


# --- multpl fetch ---------------------------------------------------------

def test_multpl_writes_provenance(tmp_macro_dir: Path):
    state = init_state(tmp_macro_dir, "_MACRO")
    ok, _paths, err = fetch_multpl_series("sp500_pe", tmp_macro_dir, state,
                                          html_provider=lambda u: _multpl_fixture(),
                                          now=NOW)
    assert ok and err is None
    meta, data = read_structured(tmp_macro_dir / "structured" / "sp500_pe.json")
    assert meta.ticker == "_MACRO"
    assert meta.as_of == "2026-07-30"
    assert data["observations"][0]["value"] == 28.41
    assert state["data"]["sp500_pe"]["policy_days"] == 30
    assert _errors(tmp_macro_dir) == []


def test_the_five_spec_series_are_registered():
    assert set(MULTPL_SERIES) == {"sp500_pe", "shiller_pe_cape",
                                  "sp500_dividend_yield", "sp500_earnings_yield",
                                  "sp500_price_real"}


def test_a_shape_failure_is_returned_not_raised(tmp_macro_dir: Path):
    """The fetcher's contract absorbs the loud parse failure so prefetch-macro
    can report it as a warning (§12.3)."""
    state = init_state(tmp_macro_dir, "_MACRO")
    ok, _paths, err = fetch_multpl_series(
        "sp500_pe", tmp_macro_dir, state,
        html_provider=lambda u: "<html><body>nothing</body></html>", now=NOW)
    assert not ok and "shape check failed" in err


def test_an_unknown_series_is_rejected(tmp_macro_dir: Path):
    state = init_state(tmp_macro_dir, "_MACRO")
    ok, _paths, err = fetch_multpl_series("not_a_series", tmp_macro_dir, state,
                                          html_provider=lambda u: "", now=NOW)
    assert not ok and "unknown multpl series" in err


# --- prefetch-macro CLI ---------------------------------------------------

def test_prefetch_macro_needs_an_initialized_macro_tree(tmp_path: Path):
    assert sra.main(["prefetch-macro", "--data-root", str(tmp_path)]) == 1


def test_prefetch_macro_rejects_an_unknown_series(tmp_path: Path):
    sra.main(["init", "_MACRO", "--data-root", str(tmp_path)])
    assert sra.main(["prefetch-macro", "--series", "nope",
                     "--data-root", str(tmp_path)]) == 1


def test_a_failed_series_is_a_warning_not_a_failure(tmp_path: Path, capsys,
                                                    monkeypatch):
    """§12.3: "A failed macro series is a warning." Macro data is context for
    every ticker; one dead series must not block a build."""
    sra.main(["init", "_MACRO", "--data-root", str(tmp_path)])
    monkeypatch.setattr(sra.multpl, "_fetch_html",
                        lambda url: "<html><body>markup moved</body></html>")
    capsys.readouterr()
    assert sra.main(["prefetch-macro", "--series", "sp500_pe",
                     "--data-root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "sp500_pe" in out["errors"]


def test_prefetch_macro_writes_into_the_macro_tree(tmp_path: Path, capsys,
                                                   monkeypatch):
    sra.main(["init", "_MACRO", "--data-root", str(tmp_path)])
    monkeypatch.setattr(sra.multpl, "_fetch_html", lambda url: _multpl_fixture())
    capsys.readouterr()
    assert sra.main(["prefetch-macro", "--series", "sp500_pe",
                     "--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["fetched"] == ["sp500_pe"]
    assert (tmp_path / "_MACRO" / "structured" / "sp500_pe.json").is_file()


def test_prefetch_macro_stale_only_skips_fresh_series(tmp_path: Path, capsys,
                                                      monkeypatch):
    sra.main(["init", "_MACRO", "--data-root", str(tmp_path)])
    monkeypatch.setattr(sra.multpl, "_fetch_html", lambda url: _multpl_fixture())
    sra.main(["prefetch-macro", "--series", "sp500_pe", "--data-root", str(tmp_path)])
    capsys.readouterr()

    sra.main(["prefetch-macro", "--series", "sp500_pe", "--stale-only",
              "--data-root", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == ["sp500_pe"]
    assert out["fetched"] == []


def test_prefetch_macro_takes_the_lock(tmp_path: Path):
    sra.main(["init", "_MACRO", "--data-root", str(tmp_path)])
    (tmp_path / "_MACRO" / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "prefetch-macro",
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    assert sra.main(["prefetch-macro", "--data-root", str(tmp_path)]) == 1
