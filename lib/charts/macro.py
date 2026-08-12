#!/usr/bin/env python3
"""Macro context exhibits from the shared `_MACRO` tree (spec §12, §16.1).

Macro evidence is fetched once and shared by every ticker, so these renderers
read `data/_MACRO/structured/` rather than the ticker's own tree — the one place
a chart reaches outside the directory it was called with, and the reason the
lookup is a single explicit helper rather than scattered path arithmetic.

Two exhibits: the Treasury curve (the discount rate every valuation section
argues about) and the S&P 500's own multiple (the market the subject's multiple
is quoted against). Both degrade to `None` when `prefetch-macro` has not run.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from lib.charts.base import (
    CATEGORICAL, MUTED, RULE, SMALL_AXIS_TITLE_FONT, ChartResult, FONT_FAMILY,
    apply_base_layout, write_candidate)
from lib.charts.common import number, read_artifact, read_meta

MACRO_TICKER = "_MACRO"
MACRO_HEIGHT = 420

# How much history an exhibit shows. Longer than the price chart's four years:
# the point of a macro panel is where today sits in a regime, and a rate series
# truncated to the current regime cannot show that.
MACRO_YEARS = 10

RATE_SERIES = (("fred_dgs10", "10-year Treasury"), ("fred_dgs2", "2-year Treasury"))
VALUATION_SERIES = (("shiller_pe_cape", "Shiller P/E (CAPE)"),
                    ("sp500_pe", "S&P 500 trailing P/E"))


def macro_dir(ticker_dir: Path) -> Path:
    """The shared macro tree beside this ticker's directory (§12)."""
    return ticker_dir.parent / MACRO_TICKER


def observations(macro: Path, artifact_id: str,
                 since: date | None = None) -> list[tuple[str, float]] | None:
    """`[(date, value)]` for a macro series, oldest first, or `None`.

    FRED marks a missing observation with `"."` and multpl simply omits the row;
    either way the point is dropped rather than carried forward (§6.4). A rate
    that did not print is not yesterday's rate.
    """
    data = read_artifact(macro, artifact_id)
    if not data:
        return None
    rows = data.get("observations")
    if not isinstance(rows, list):
        return None

    out: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        when = str(row.get("date") or "")[:10]
        value = number(row.get("value"))
        if len(when) != 10 or value is None:
            continue
        if since and when < since.isoformat():
            continue
        out.append((when, value))
    out.sort()
    return out or None


def _series_source(macro: Path, artifact_id: str) -> str:
    return str((read_meta(macro, artifact_id) or {}).get("source") or "unknown")


def _line_chart(macro: Path, series: tuple[tuple[str, str], ...],
                since: date) -> tuple[object, list[str], list[str], dict] | None:
    """A multi-series line figure plus the ids, sources and latest values."""
    import plotly.graph_objects as go

    fig = go.Figure()
    used_ids: list[str] = []
    sources: list[str] = []
    latest: dict[str, tuple[str, float]] = {}

    for (artifact_id, label), color in zip(series, CATEGORICAL):
        points = observations(macro, artifact_id, since)
        if not points:
            continue
        used_ids.append(artifact_id)
        sources.append(_series_source(macro, artifact_id))
        xs = [d for d, _ in points]
        ys = [v for _, v in points]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", connectgaps=False,
                                 name=label, line=dict(color=color, width=1.5)))
        fig.add_annotation(x=xs[-1], y=ys[-1], text=f"{label} {ys[-1]:.2f}",
                           xanchor="left", yanchor="middle", xshift=6,
                           showarrow=False,
                           font=dict(family=FONT_FAMILY, size=10, color=color))
        latest[label] = (xs[-1], ys[-1])

    if not used_ids:
        return None
    return fig, used_ids, sources, latest


def _since(now: datetime | None) -> date:
    today = (now or datetime.now(timezone.utc)).date()
    return date(today.year - MACRO_YEARS, today.month, today.day)


def render_macro_rates(ticker_dir: Path, *, write_png: bool = True,
                       now: datetime | None = None) -> ChartResult | None:
    """The 10-year and 2-year Treasury yields over the last decade."""
    macro = macro_dir(ticker_dir)
    built = _line_chart(macro, RATE_SERIES, _since(now))
    if built is None:
        return None
    fig, used_ids, sources, latest = built

    apply_base_layout(fig, height=MACRO_HEIGHT)
    fig.update_yaxes(title_text="Yield (%)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT)

    body = [f"{label} at {value:.2f}% ({when})"
            for label, (when, value) in latest.items()]
    if "10-year Treasury" in latest and "2-year Treasury" in latest:
        # The 10s-2s spread is the number the valuation section actually argues
        # about, and reading it off two lines by eye is exactly the work a
        # caption should do for the reader.
        spread = latest["10-year Treasury"][1] - latest["2-year Treasury"][1]
        body.append(f"10s-2s spread at {spread:+.2f}pp")

    return write_candidate(
        ticker_dir, fig,
        name="macro_rates",
        title=f"Treasury yields, last {MACRO_YEARS} years",
        data_sources=used_ids,
        auto_caption="; ".join(body) + f". Source: {sources[0]}.",
        salience={
            "recency_days": 0,
            "coverage": round(len(used_ids) / len(RATE_SERIES), 2),
            "variance_note": f"{MACRO_YEARS}-year window",
        },
        height=MACRO_HEIGHT, write_png=write_png)


def render_macro_market_valuation(ticker_dir: Path, *, write_png: bool = True,
                                  now: datetime | None = None) -> ChartResult | None:
    """The S&P 500's own multiples — the yardstick a subject multiple is read against."""
    macro = macro_dir(ticker_dir)
    built = _line_chart(macro, VALUATION_SERIES, _since(now))
    if built is None:
        return None
    fig, used_ids, sources, latest = built

    apply_base_layout(fig, height=MACRO_HEIGHT)
    fig.update_yaxes(title_text="Price / earnings (x)", ticksuffix="x",
                     title_font=SMALL_AXIS_TITLE_FONT)

    body = [f"{label} at {value:.1f}x ({when})"
            for label, (when, value) in latest.items()]
    return write_candidate(
        ticker_dir, fig,
        name="macro_market_valuation",
        title=f"S&P 500 valuation, last {MACRO_YEARS} years",
        data_sources=used_ids,
        auto_caption="; ".join(body) + f". Source: {sources[0]}.",
        salience={
            "recency_days": 0,
            "coverage": round(len(used_ids) / len(VALUATION_SERIES), 2),
            "variance_note": f"{MACRO_YEARS}-year window",
        },
        height=MACRO_HEIGHT, write_png=write_png)
