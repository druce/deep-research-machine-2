"""The weekly price exhibit (spec §17.2).

Structural tests over the figure object, not the pixels: every rule in §17.2 is
a property of a trace or an axis, and asserting on the figure catches a drifted
color or a resurrected secondary axis in milliseconds instead of needing someone
to look at a PNG.

The chart is a pure function of `prices_yahoo` — the weekly bars, the two moving
averages and the relative-strength line are all resampled from the daily series
the fetcher already stored, so the exhibit and the indicators provably describe
the same data and the whole thing is testable offline.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from lib.charts import base, price

WEEKS = 260          # five years of daily bars: more than the 4y display cap


def _prices_payload(*, weeks: int = WEEKS, benchmark: bool = True) -> dict:
    """A synthetic daily OHLCV series in `prices_yahoo`'s stored shape."""
    start = date(2021, 1, 4)
    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for i in range(weeks * 7):
        day = start + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        close = 100 + 30 * math.sin(i / 40) + i * 0.05
        dates.append(day.isoformat())
        opens.append(round(close - 0.5, 2))
        highs.append(round(close + 1.2, 2))
        lows.append(round(close - 1.4, 2))
        closes.append(round(close, 2))
        volumes.append(1_000_000 + (i % 7) * 50_000)

    bench = None
    if benchmark:
        bench = {"symbol": "^GSPC", "dates": dates,
                 "close": [round(4000 + i * 0.6, 2) for i in range(len(dates))]}
    return {"daily": {"dates": dates, "open": opens, "high": highs, "low": lows,
                      "close": closes, "volume": volumes},
            "benchmark": bench}


def write_prices(ticker_dir: Path, payload: dict | None = None) -> Path:
    payload = payload or _prices_payload()
    dates = payload["daily"]["dates"]
    path = ticker_dir / "structured" / "prices_yahoo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_meta": {"id": "prices_yahoo", "ticker": "PANW", "producer": "fetch",
                  "title": "PANW daily OHLCV prices", "source": "Yahoo Finance",
                  "url": "https://finance.yahoo.com/quote/PANW/history",
                  "provider_tool": "yfinance.download",
                  "fetch_cmd": "uv run python sra.py prefetch PANW --kinds prices",
                  "fetched_at": "2026-08-11T12:00:00+00:00",
                  "as_of": dates[-1] if dates else "2026-08-07", "adjusted": True},
        "data": payload}), encoding="utf-8")
    return path


def write_technical(ticker_dir: Path) -> Path:
    path = ticker_dir / "structured" / "technical_indicators_computed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_meta": {"id": "technical_indicators_computed", "ticker": "PANW",
                  "producer": "compute", "title": "PANW technical indicators",
                  "source": "computed",
                  "provider_tool": "lib/fetchers/technical.py",
                  "fetch_cmd": "uv run python sra.py prefetch PANW --kinds technical",
                  "computed_at": "2026-08-11T12:00:00+00:00",
                  "as_of": "2026-08-07", "derived_from": ["prices_yahoo"]},
        "data": {"symbol": "PANW", "date": "2026-08-07", "close": 212.5,
                 "indicators": {"rsi": 58.2, "sma_50": 205.0, "sma_200": 190.0},
                 "trend_signals": {"trend": "uptrend"}}}), encoding="utf-8")
    return path


@pytest.fixture
def figure(tmp_ticker_dir: Path):
    write_prices(tmp_ticker_dir)
    frame = price.weekly_frame(price.read_prices(tmp_ticker_dir))
    return price.build_figure("PANW", frame)


def traces_of(fig, kind: str) -> list:
    return [t for t in fig.data if t.type == kind]


def scatter_named(fig, name: str):
    return next(t for t in fig.data if getattr(t, "name", None) == name)


# --- panels ----------------------------------------------------------------

def test_three_panels_in_the_spec_proportions(figure):
    """§17.2: `row_heights=[0.62, 0.16, 0.22]`, `vertical_spacing=0.035`."""
    domains = [figure.layout[axis].domain for axis in ("yaxis", "yaxis2", "yaxis3")]
    heights = [hi - lo for lo, hi in domains]
    # The spacing eats into the total, so compare shares rather than raw spans.
    shares = [h / sum(heights) for h in heights]
    assert shares == pytest.approx([0.62, 0.16, 0.22], abs=0.01)
    # Row 1 sits on top: its floor is one gap above row 2's ceiling.
    assert domains[0][0] - domains[1][1] == pytest.approx(0.035, abs=0.005)


def test_never_a_secondary_y_axis(figure):
    """§17.2: two measures of different scale get two panels. A secondary axis
    leaves the reader unable to tell which curve owns which scale."""
    for name, axis in figure.layout.to_plotly_json().items():
        if name.startswith("yaxis") and isinstance(axis, dict):
            assert not axis.get("overlaying"), name
            assert axis.get("side") != "right", name


def test_geometry_and_no_legend(figure):
    assert figure.layout.width == base.CHART_WIDTH
    assert figure.layout.height == base.PRICE_HEIGHT
    assert figure.layout.showlegend is False
    assert figure.layout.title.text is None


# --- row 1: candles and moving averages ------------------------------------

def test_candles_encode_direction_by_shape_not_only_color():
    """§17.1: "Red/green color alone is insufficient for candlestick direction.
    Shape encoding is mandatory." Up candles are hollow — a transparent fill —
    so direction survives a desaturated print."""
    payload = _prices_payload(weeks=40)
    frame = price.weekly_frame(payload)
    fig = price.build_figure("PANW", frame)
    candles = traces_of(fig, "candlestick")
    assert len(candles) == 1
    candle = candles[0]

    assert candle.increasing.fillcolor == "rgba(0,0,0,0)"
    assert candle.increasing.line.color == base.UP
    assert candle.decreasing.fillcolor == base.DOWN
    assert candle.decreasing.line.color == base.DOWN
    assert candle.increasing.line.width == 1
    assert candle.decreasing.line.width == 1


def test_candle_x_values_are_iso_strings(figure):
    """kaleido 1.3 dumps the figure with orjson, which refuses `pd.Timestamp`
    (a datetime subclass) — and only at PNG-write time, long after the figure
    looked fine in a test."""
    candle = traces_of(figure, "candlestick")[0]
    assert all(isinstance(x, str) for x in candle.x)
    assert candle.x[0].count("-") == 2


def test_history_is_capped_at_four_years_weekly(figure):
    """§17.2: "Maximum default history: 4 years weekly"."""
    candle = traces_of(figure, "candlestick")[0]
    assert len(candle.x) <= price.MAX_WEEKS
    assert len(candle.x) == price.MAX_WEEKS      # the fixture holds five years


def test_moving_averages_wear_slots_one_and_two(figure):
    ma13 = scatter_named(figure, "MA13")
    ma52 = scatter_named(figure, "MA52")
    assert ma13.line.color == base.S1_MA13
    assert ma52.line.color == base.S2_MA52
    assert ma13.line.width == 1.75 and ma52.line.width == 1.75


# --- row 2: volume ---------------------------------------------------------

def test_volume_is_one_muted_color_never_direction_colored(figure):
    """§17.2: "Do not color volume by price direction." A per-bar color list is
    exactly that mistake, and it re-encodes what the panel above already says."""
    bars = traces_of(figure, "bar")
    assert len(bars) == 1
    assert bars[0].marker.color == base.S3_VOLUME
    assert isinstance(bars[0].marker.color, str)
    assert bars[0].opacity == 0.45


# --- row 3: relative strength ----------------------------------------------

def test_relative_strength_is_slot_four_with_a_parity_line(figure):
    rs = scatter_named(figure, "RS")
    assert rs.line.color == base.S4_RS
    assert rs.line.width == 1.75
    assert rs.y[0] == pytest.approx(1.0)

    parity = [s for s in figure.layout.shapes if s.type == "line"
              and s.y0 == 1.0 and s.y1 == 1.0]
    assert parity, "§17.2 requires the y=1.0 parity line"
    assert parity[0].line.color == base.RULE


def test_parity_line_is_annotated_and_the_axis_carries_the_unit(figure):
    """§17.2: annotation "= S&P 500", axis title "vs S&P 500, indexed to 1.0 at
    start" — §17.4 requires units visible on the chart."""
    texts = [a.text for a in figure.layout.annotations]
    assert any("= S&P 500" in t for t in texts)
    assert figure.layout.yaxis3.title.text == "vs S&P 500, indexed to 1.0 at start"


def test_the_rs_panel_is_simply_absent_without_a_benchmark(tmp_ticker_dir: Path):
    """The benchmark is optional in `prices_yahoo`, so the panel degrades
    rather than plotting a flat line that reads as parity."""
    frame = price.weekly_frame(_prices_payload(benchmark=False))
    fig = price.build_figure("PANW", frame)
    assert not [t for t in fig.data if getattr(t, "name", None) == "RS"]
    assert not fig.layout.yaxis3.title.text


# --- direct labels ---------------------------------------------------------

def test_every_series_is_directly_labeled_at_its_final_x(figure):
    """§17.2: "No legend. Direct-label each series at its final x-value." The
    labels carry identity in text and color together, which a low-contrast
    legend swatch cannot."""
    labels = {a.text: a for a in figure.layout.annotations}
    for name, color in (("PANW", base.INK), ("MA13", base.S1_MA13),
                        ("MA52", base.S2_MA52), ("RS", base.S4_RS)):
        assert name in labels, name
        assert labels[name].font.color == color


def test_converging_labels_are_spread_apart():
    """Two series ending at the same value print on top of each other; the
    spreader preserves their true vertical order while clearing a gap."""
    spread = price.spread_label_positions([10.0, 10.05, 10.1], min_sep=1.0)
    assert spread == sorted(spread)
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(spread, spread[1:]))


def test_spreading_keeps_the_true_ordering():
    spread = price.spread_label_positions([12.0, 10.0], min_sep=1.0)
    assert spread[1] < spread[0]


# --- the renderer ----------------------------------------------------------

def test_renderer_writes_a_candidate_and_declares_its_lineage(tmp_ticker_dir: Path):
    write_prices(tmp_ticker_dir)
    write_technical(tmp_ticker_dir)
    result = price.render_price_weekly(tmp_ticker_dir, write_png=False)

    assert isinstance(result, base.ChartResult)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["name"] == "price_weekly"
    assert manifest["data_sources"] == ["prices_yahoo",
                                        "technical_indicators_computed"]
    assert "Yahoo Finance" in manifest["auto_caption"]
    assert manifest["salience"]["recency_days"] >= 0


def test_renderer_declares_only_the_inputs_it_actually_read(tmp_ticker_dir: Path):
    """The technical artifact is optional — it contributes the caption's trend
    note. Listing it when it is absent would claim a lineage that is not there."""
    write_prices(tmp_ticker_dir)
    result = price.render_price_weekly(tmp_ticker_dir, write_png=False)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["data_sources"] == ["prices_yahoo"]


def test_renderer_returns_none_without_prices(tmp_ticker_dir: Path):
    """§16.1: missing inputs are normal degraded behavior, not an error."""
    assert price.render_price_weekly(tmp_ticker_dir, write_png=False) is None


def test_renderer_returns_none_on_an_unusable_series(tmp_ticker_dir: Path):
    write_prices(tmp_ticker_dir, {"daily": {"dates": [], "open": [], "high": [],
                                            "low": [], "close": [], "volume": []},
                                  "benchmark": None})
    assert price.render_price_weekly(tmp_ticker_dir, write_png=False) is None


def test_renderer_is_registered_for_the_verdict_independent_pass():
    from lib.charts import registry

    assert registry.RENDERERS["price_weekly"].requires_verdict is False
