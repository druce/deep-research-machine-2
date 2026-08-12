"""The Tier-1 fundamental, peer, calendar and macro renderers (§16.1, §6.4).

Three properties get tested for every renderer, because they are what make a
chart trustworthy rather than decorative:

1. **It degrades.** Missing inputs return `None` — §16.1's normal behavior — and
   never raise into the render loop.
2. **It never interpolates.** A period the provider did not report is a break
   (`connectgaps=False`) and a disclosed clause in the caption, not a straight
   line drawn through numbers nobody published (§6.4).
3. **It never fetches.** The socket guard below is an autouse fixture over the
   whole module: charts are functions of persisted artifacts (§16.3), so any
   renderer that reaches for a provider fails here.
"""
from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lib.charts import calendar, fundamentals, macro, peers, registry

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
B = 1_000_000_000


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """§16.3: "No chart performs network fetches."."""
    def refuse(*args, **kwargs):
        raise AssertionError("a renderer opened a socket — charts are functions "
                             "of persisted artifacts (§16.3)")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# --- fixtures ---------------------------------------------------------------

def write_structured(ticker_dir: Path, artifact_id: str, data: dict,
                     *, source: str = "Yahoo Finance",
                     as_of: str = "2026-07-31") -> Path:
    path = ticker_dir / "structured" / f"{artifact_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_meta": {"id": artifact_id, "ticker": ticker_dir.name, "producer": "fetch",
                  "title": artifact_id, "source": source,
                  "url": "https://finance.yahoo.com/",
                  "provider_tool": "yfinance", "fetch_cmd": "sra.py prefetch",
                  "fetched_at": NOW.isoformat(), "as_of": as_of},
        "data": data}), encoding="utf-8")
    return path


def income(periods: dict[str, dict] | None = None) -> dict:
    return periods if periods is not None else {
        "2023-07-31": {"Total Revenue": 5.0 * B, "Gross Profit": 3.4 * B,
                       "Operating Income": 0.4 * B, "Net Income": 0.3 * B,
                       "Diluted EPS": 1.0},
        "2024-07-31": {"Total Revenue": 6.2 * B, "Gross Profit": 4.3 * B,
                       "Operating Income": 0.7 * B, "Net Income": 0.6 * B,
                       "Diluted EPS": 1.9},
        "2025-07-31": {"Total Revenue": 7.1 * B, "Gross Profit": 5.0 * B,
                       "Operating Income": 1.0 * B, "Net Income": 0.9 * B,
                       "Diluted EPS": 2.7},
        "2026-07-31": {"Total Revenue": 8.0 * B, "Gross Profit": 5.6 * B,
                       "Operating Income": 1.4 * B, "Net Income": 1.1 * B,
                       "Diluted EPS": 3.2},
    }


def cashflow() -> dict:
    return {
        "2024-07-31": {"Operating Cash Flow": 1.8 * B,
                       "Capital Expenditure": -0.3 * B},
        "2025-07-31": {"Operating Cash Flow": 2.3 * B,
                       "Capital Expenditure": -0.35 * B},
        "2026-07-31": {"Free Cash Flow": 2.6 * B},
    }


def prices() -> dict:
    dates, closes = [], []
    start = datetime(2022, 1, 3, tzinfo=timezone.utc).date()
    for i in range(1200):
        day = start + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        dates.append(day.isoformat())
        closes.append(round(80 + i * 0.12, 2))
    return {"daily": {"dates": dates, "open": closes, "high": closes,
                      "low": closes, "close": closes,
                      "volume": [1_000_000] * len(dates)},
            "benchmark": None}


def ratios(*, forward_pe: float | None = 42.5, growth: float | None = 0.14,
           margin: float | None = 0.175, cap: float | None = 92.0 * B) -> dict:
    return {
        "valuation": {"forward_pe": forward_pe, "trailing_pe": 61.0,
                      "ev_to_revenue": 11.2},
        "highlights": {"market_cap": cap, "revenue_ttm": 8.0 * B,
                       "revenue_growth_yoy": growth},
        "profitability": {"operating_margin": margin, "gross_margin": 0.70},
        "liquidity": {}, "per_share": {},
    }


def write_peer(data_root: Path, symbol: str, **kwargs) -> Path:
    peer_dir = data_root / symbol
    write_structured(peer_dir, "key_ratios_computed", ratios(**kwargs))
    return peer_dir


def write_selection(ticker_dir: Path, symbols: list[str]) -> Path:
    path = ticker_dir / "derived" / "peers" / "peers_selected.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_meta": {"id": "peers_selected", "ticker": ticker_dir.name,
                  "producer": "model", "title": "selected peers",
                  "source": "sra-rater", "generated_at": NOW.isoformat(),
                  "as_of": "2026-08-11", "derived_from": ["peers_candidates"]},
        "data": {"peers": [{"symbol": s} for s in symbols], "runners_up": [],
                 "origin": "model_rated", "warnings": []}}), encoding="utf-8")
    return path


def write_macro_series(macro_dir: Path, artifact_id: str, rows: list[dict],
                       *, source: str = "FRED (Federal Reserve Bank of St. Louis)"):
    write_structured(macro_dir, artifact_id, {"observations": rows}, source=source)


def daily_rate_rows(value: float, *, years: int = 6, missing: bool = False) -> list[dict]:
    rows = []
    start = NOW.date() - timedelta(days=365 * years)
    for i in range(0, 365 * years, 30):
        when = (start + timedelta(days=i)).isoformat()
        rows.append({"date": when,
                     "value": "." if missing and i == 60 else f"{value + i / 3650:.2f}"})
    return rows


def manifest_of(result) -> dict:
    return json.loads(result.manifest_path.read_text())


# --- revenue and growth -----------------------------------------------------

def test_revenue_growth_renders_two_panels(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "income_statement_yahoo", income())
    result = fundamentals.render_revenue_growth(tmp_ticker_dir, write_png=False)
    assert result is not None
    manifest = manifest_of(result)
    assert manifest["data_sources"] == ["income_statement_yahoo"]
    assert "$8.0B" in manifest["auto_caption"]
    assert "Yahoo Finance" in manifest["auto_caption"]


def test_revenue_growth_needs_two_periods_to_compute_growth(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "income_statement_yahoo",
                     {"2026-07-31": {"Total Revenue": 8.0 * B}})
    assert fundamentals.render_revenue_growth(tmp_ticker_dir,
                                              write_png=False) is None


def test_revenue_growth_returns_none_without_the_statement(tmp_ticker_dir: Path):
    assert fundamentals.render_revenue_growth(tmp_ticker_dir,
                                              write_png=False) is None


# --- margins ----------------------------------------------------------------

def test_margin_trends_plots_three_margins(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "income_statement_yahoo", income())
    result = fundamentals.render_margin_trends(tmp_ticker_dir, write_png=False)
    caption = manifest_of(result)["auto_caption"]
    assert "gross" in caption and "operating" in caption and "net" in caption


def test_a_missing_period_is_a_break_not_a_bridge(tmp_ticker_dir: Path):
    """§6.4: no interpolation. The line breaks and the caption says where."""
    periods = income()
    del periods["2024-07-31"]["Operating Income"]
    write_structured(tmp_ticker_dir, "income_statement_yahoo", periods)

    result = fundamentals.render_margin_trends(tmp_ticker_dir, write_png=False)
    caption = manifest_of(result)["auto_caption"]
    assert "Gaps:" in caption and "2024-07-31" in caption


def test_every_line_refuses_to_connect_across_gaps(tmp_ticker_dir: Path):
    periods = income()
    del periods["2024-07-31"]["Operating Income"]
    write_structured(tmp_ticker_dir, "income_statement_yahoo", periods)

    from lib.charts.common import statement_series
    data = periods
    series = statement_series(data, "Operating Income")
    assert series["2024-07-31"] is None      # absent, never zero-filled


# --- free cash flow ---------------------------------------------------------

def test_fcf_uses_reported_free_cash_flow_when_present(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "cashflow_yahoo", cashflow())
    write_structured(tmp_ticker_dir, "income_statement_yahoo", income())
    result = fundamentals.render_fcf_conversion(tmp_ticker_dir, write_png=False)
    caption = manifest_of(result)["auto_caption"]
    assert "$2.6B" in caption


def test_fcf_derives_from_operating_cash_flow_and_capex(tmp_ticker_dir: Path):
    """Capex arrives negative from this provider, so free cash flow is OCF PLUS
    capex — subtracting would double it."""
    write_structured(tmp_ticker_dir, "cashflow_yahoo",
                     {"2025-07-31": {"Operating Cash Flow": 2.0 * B,
                                     "Capital Expenditure": -0.5 * B}})
    write_structured(tmp_ticker_dir, "income_statement_yahoo", income())
    result = fundamentals.render_fcf_conversion(tmp_ticker_dir, write_png=False)
    assert "$1.5B" in manifest_of(result)["auto_caption"]


def test_fcf_needs_both_statements(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "cashflow_yahoo", cashflow())
    assert fundamentals.render_fcf_conversion(tmp_ticker_dir,
                                              write_png=False) is None


# --- forward multiple -------------------------------------------------------

def test_forward_multiple_needs_all_three_inputs(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "income_statement_yahoo", income())
    write_structured(tmp_ticker_dir, "prices_yahoo", prices())
    assert fundamentals.render_forward_multiple(tmp_ticker_dir,
                                                write_png=False) is None

    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    result = fundamentals.render_forward_multiple(tmp_ticker_dir, write_png=False)
    assert result is not None
    assert manifest_of(result)["data_sources"] == [
        "income_statement_yahoo", "prices_yahoo", "key_ratios_computed"]


def test_forward_multiple_reports_the_forward_point(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "income_statement_yahoo", income())
    write_structured(tmp_ticker_dir, "prices_yahoo", prices())
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    result = fundamentals.render_forward_multiple(tmp_ticker_dir, write_png=False)
    assert "42.5x" in manifest_of(result)["auto_caption"]


def test_forward_multiple_skips_loss_making_years(tmp_ticker_dir: Path):
    """A negative EPS makes P/E meaningless, not negative."""
    periods = income()
    periods["2023-07-31"]["Diluted EPS"] = -0.5
    write_structured(tmp_ticker_dir, "income_statement_yahoo", periods)
    write_structured(tmp_ticker_dir, "prices_yahoo", prices())
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    result = fundamentals.render_forward_multiple(tmp_ticker_dir, write_png=False)
    assert "3 years" in manifest_of(result)["auto_caption"]


# --- peers ------------------------------------------------------------------

def test_peer_charts_read_each_peer_s_own_bronze(tmp_ticker_dir: Path):
    """§13.6: the selection file is lineage, the peers' own artifacts are the
    evidence."""
    root = tmp_ticker_dir.parent
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    write_selection(tmp_ticker_dir, ["CRWD", "ZS"])
    write_peer(root, "CRWD", forward_pe=70.0, growth=0.28, margin=0.09)
    write_peer(root, "ZS", forward_pe=55.0, growth=0.22, margin=0.05)

    result = peers.render_peer_multiples(tmp_ticker_dir, write_png=False)
    manifest = manifest_of(result)
    assert manifest["data_sources"] == ["peers_selected", "key_ratios_computed",
                                        "CRWD:key_ratios_computed",
                                        "ZS:key_ratios_computed"]
    assert "55.0x" in manifest["auto_caption"] or "peer median" in \
        manifest["auto_caption"]


def test_a_peer_without_bronze_is_disclosed_not_estimated(tmp_ticker_dir: Path):
    """§6.4: gaps are disclosed. Filling the row from the silver candidate table
    would put a number on the page whose provenance runs through a ranking."""
    root = tmp_ticker_dir.parent
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    write_selection(tmp_ticker_dir, ["CRWD", "NEVERBUILT"])
    write_peer(root, "CRWD", forward_pe=70.0)

    result = peers.render_peer_multiples(tmp_ticker_dir, write_png=False)
    caption = manifest_of(result)["auto_caption"]
    assert "NEVERBUILT" in caption
    assert "excluded rather than estimated" in caption


def test_peer_scatter_plots_growth_against_margin(tmp_ticker_dir: Path):
    root = tmp_ticker_dir.parent
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    write_selection(tmp_ticker_dir, ["CRWD", "ZS"])
    write_peer(root, "CRWD", growth=0.28, margin=0.09)
    write_peer(root, "ZS", growth=0.22, margin=0.05)

    result = peers.render_peer_scatter(tmp_ticker_dir, write_png=False)
    assert "market capitalization" in manifest_of(result)["auto_caption"]


def test_peer_charts_need_at_least_two_companies(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    write_selection(tmp_ticker_dir, ["NEVERBUILT"])
    assert peers.render_peer_multiples(tmp_ticker_dir, write_png=False) is None
    assert peers.render_peer_scatter(tmp_ticker_dir, write_png=False) is None


def test_peer_charts_need_a_selection(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "key_ratios_computed", ratios())
    assert peers.render_peer_multiples(tmp_ticker_dir, write_png=False) is None


def test_peer_charts_need_the_subject_s_own_bronze(tmp_ticker_dir: Path):
    write_selection(tmp_ticker_dir, ["CRWD", "ZS"])
    write_peer(tmp_ticker_dir.parent, "CRWD")
    write_peer(tmp_ticker_dir.parent, "ZS")
    assert peers.render_peer_multiples(tmp_ticker_dir, write_png=False) is None


# --- calendar ---------------------------------------------------------------

def calendar_data() -> dict:
    return {"calendar": {"Earnings Date": ["2026-08-25"],
                         "Ex-Dividend Date": "2026-09-10"},
            "earnings_dates": {
                "2026-05-20": {"Surprise(%)": 4.2},
                "2026-02-18": {"Surprise(%)": -1.1},
                "2025-11-19": {"Surprise(%)": 2.8}}}


def test_calendar_reports_the_next_catalyst(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "events_calendar_yahoo", calendar_data())
    result = calendar.render_catalyst_calendar(tmp_ticker_dir, write_png=False,
                                               now=NOW)
    caption = manifest_of(result)["auto_caption"]
    assert "2026-08-25" in caption and "14 days" in caption


def test_forward_event_labels_are_stacked_not_overprinted():
    """Two catalysts weeks apart put their labels at nearly the same x. Pinned
    to the same y they render as an unreadable smear — which is what the first
    version of this chart actually did."""
    reported, forward = calendar.events(calendar_data())
    assert len(forward) == 2

    fig = calendar.build_figure(reported, forward, NOW.date())
    event_labels = [a for a in fig.layout.annotations
                    if any(label in (a.text or "") for _, label in forward)]
    assert len(event_labels) == 2
    assert event_labels[0].y != event_labels[1].y


def test_calendar_renders_from_earnings_history_alone(tmp_ticker_dir: Path):
    data = calendar_data()
    data["calendar"] = {}
    write_structured(tmp_ticker_dir, "events_calendar_yahoo", data)
    result = calendar.render_catalyst_calendar(tmp_ticker_dir, write_png=False,
                                               now=NOW)
    assert result is not None
    assert "last 3 quarters" in manifest_of(result)["auto_caption"]


def test_calendar_discloses_missing_surprise_data(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "events_calendar_yahoo",
                     {"calendar": {}, "earnings_dates": {"2026-05-20": {}}})
    result = calendar.render_catalyst_calendar(tmp_ticker_dir, write_png=False,
                                               now=NOW)
    assert "was not reported" in manifest_of(result)["auto_caption"]


def test_calendar_returns_none_when_both_halves_are_empty(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, "events_calendar_yahoo",
                     {"calendar": {}, "earnings_dates": {}})
    assert calendar.render_catalyst_calendar(tmp_ticker_dir,
                                             write_png=False) is None


# --- macro ------------------------------------------------------------------

def test_macro_rates_reads_the_shared_tree(tmp_ticker_dir: Path,
                                           tmp_macro_dir: Path):
    """§12: macro is fetched once and shared, so it lives beside the ticker."""
    assert macro.macro_dir(tmp_ticker_dir) == tmp_macro_dir
    write_macro_series(tmp_macro_dir, "fred_dgs10", daily_rate_rows(3.9))
    write_macro_series(tmp_macro_dir, "fred_dgs2", daily_rate_rows(3.4))

    result = macro.render_macro_rates(tmp_ticker_dir, write_png=False, now=NOW)
    manifest = manifest_of(result)
    assert manifest["data_sources"] == ["fred_dgs10", "fred_dgs2"]
    assert "spread" in manifest["auto_caption"]
    assert "FRED" in manifest["auto_caption"]


def test_macro_rates_renders_with_one_series_and_says_so(tmp_ticker_dir: Path,
                                                        tmp_macro_dir: Path):
    write_macro_series(tmp_macro_dir, "fred_dgs10", daily_rate_rows(3.9))
    result = macro.render_macro_rates(tmp_ticker_dir, write_png=False, now=NOW)
    manifest = manifest_of(result)
    assert manifest["data_sources"] == ["fred_dgs10"]
    assert manifest["salience"]["coverage"] == 0.5
    assert "spread" not in manifest["auto_caption"]


def test_a_dot_observation_is_dropped_not_carried_forward(tmp_ticker_dir: Path,
                                                          tmp_macro_dir: Path):
    """FRED writes "." for a day with no print. §6.4: a rate that did not print
    is not yesterday's rate."""
    write_macro_series(tmp_macro_dir, "fred_dgs10",
                       daily_rate_rows(3.9, missing=True))
    points = macro.observations(tmp_macro_dir, "fred_dgs10")
    assert all(isinstance(v, float) for _, v in points)
    assert len(points) == len(daily_rate_rows(3.9)) - 1


def test_macro_valuation_reads_multpl_series(tmp_ticker_dir: Path,
                                             tmp_macro_dir: Path):
    write_macro_series(tmp_macro_dir, "shiller_pe_cape", daily_rate_rows(31.0),
                       source="multpl.com")
    result = macro.render_macro_market_valuation(tmp_ticker_dir, write_png=False,
                                                 now=NOW)
    assert "multpl.com" in manifest_of(result)["auto_caption"]


def test_macro_renderers_return_none_without_a_macro_tree(tmp_ticker_dir: Path):
    assert macro.render_macro_rates(tmp_ticker_dir, write_png=False) is None
    assert macro.render_macro_market_valuation(tmp_ticker_dir,
                                               write_png=False) is None


# --- registry ---------------------------------------------------------------

def test_every_tier1_renderer_is_registered_without_a_verdict():
    for name in ("revenue_growth", "margin_trends", "fcf_conversion",
                 "forward_multiple_vs_history", "peer_scatter", "peer_multiples",
                 "catalyst_calendar", "macro_rates", "macro_market_valuation"):
        assert registry.RENDERERS[name].requires_verdict is False, name


def test_tier2_charts_are_absent_until_a_producer_exists():
    """§16.1: segment mix, RPO, ownership, buyback and DCF sensitivity have no
    bronze producer, so they are not implementation requirements — and a
    placeholder renderer would invent the data."""
    for absent in ("segment_mix", "geographic_mix", "rpo", "ownership",
                   "buyback_dilution", "dcf_sensitivity"):
        assert absent not in registry.RENDERERS, absent


def test_every_renderer_survives_a_bare_initialized_ticker(tmp_ticker_dir: Path):
    """The whole registry over an empty tree: every renderer degrades to None,
    none of them raises, and nothing lands in charts/candidates/."""
    for name, renderer in registry.RENDERERS.items():
        assert renderer.fn(tmp_ticker_dir) is None, name
    assert not list((tmp_ticker_dir / "charts" / "candidates").iterdir())
