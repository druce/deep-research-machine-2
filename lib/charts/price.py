#!/usr/bin/env python3
"""The weekly price / volume / relative-strength exhibit (spec §16.1, §17.2).

Three stacked panels, one scale each. An earlier version put volume (0-100M,
left axis) and relative strength (1.0-2.0, right axis) in one panel via
`secondary_y`, which leaves the reader unable to tell which curve owns which
axis — two measures of different scale need two panels, which is why §17.2
forbids secondary axes outright.

Everything here is resampled from `prices_yahoo`'s stored daily series rather
than refetched weekly bars, so the exhibit and the indicator artifact provably
describe the same data, and the renderer is a pure function of persisted
artifacts (§16.3).

Note the shape encoding on the candles. §17.1 states that red/green alone is
insufficient for direction, so up weeks are drawn hollow and down weeks filled:
direction survives a desaturated print, a monochrome photocopy, and the ~8% of
male readers with a red-green deficiency.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from lib.charts.base import (
    CHART_WIDTH, INK, MUTED, PRICE_HEIGHT, RULE, S1_MA13, S2_MA52, S3_VOLUME,
    S4_RS, SMALL_AXIS_TITLE_FONT, UP, DOWN, FONT_FAMILY, ChartResult,
    apply_base_layout, write_candidate)

MA_SHORT = 13
MA_LONG = 52

# §17.2: "Maximum default history: 4 years weekly."
MAX_WEEKS = 209                       # 4 * 52 + 1, inclusive of both endpoints

RS_AXIS_TITLE = "vs S&P 500, indexed to 1.0 at start"
PARITY_ANNOTATION = "1.0 = S&P 500"

CHART_NAME = "price_weekly"


def read_prices(ticker_dir: Path) -> dict | None:
    """`prices_yahoo`'s `data` block, or `None` when it is not on disk."""
    return _read_data(ticker_dir / "structured" / "prices_yahoo.json")


def _read_data(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def weekly_frame(prices: dict):
    """Weekly OHLCV + MA13/MA52 (+ RS when a benchmark was stored).

    Weeks close on Friday. `RS` is the ticker/benchmark close ratio indexed to
    1.0 at the first common week, and is simply absent when the fetcher stored
    no benchmark — a flat line at 1.0 would read as "exactly matched the index"
    rather than "not measured".
    """
    import pandas as pd

    daily = prices["daily"]
    if not daily.get("dates"):
        raise ValueError("empty price series")

    df = pd.DataFrame(
        {"Open": daily["open"], "High": daily["high"], "Low": daily["low"],
         "Close": daily["close"], "Volume": daily["volume"]},
        index=pd.to_datetime(daily["dates"]))
    wk = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last",
         "Volume": "sum"}).dropna(subset=["Close"])
    wk[f"MA{MA_SHORT}"] = wk["Close"].rolling(MA_SHORT).mean()
    wk[f"MA{MA_LONG}"] = wk["Close"].rolling(MA_LONG).mean()

    bench = prices.get("benchmark")
    if bench and bench.get("dates"):
        b = pd.Series(bench["close"], index=pd.to_datetime(bench["dates"]))
        b_wk = b.resample("W-FRI").last().dropna()
        common = wk.index.intersection(b_wk.index)
        if len(common) > 1:
            rs = wk.loc[common, "Close"] / b_wk.loc[common]
            wk.loc[common, "RS"] = rs / rs.iloc[0]

    # Trim AFTER the rolling means so the first displayed week still carries a
    # full 52-week average rather than a NaN gap in the corner of the panel.
    wk = wk.tail(MAX_WEEKS)
    if "RS" in wk.columns and wk["RS"].notna().any():
        # Re-index to the displayed window: an RS line whose 1.0 sits four years
        # off-screen measures against a start the reader cannot see.
        first = wk["RS"].dropna().iloc[0]
        if first:
            wk["RS"] = wk["RS"] / first
    return wk


def spread_label_positions(values: list[float], min_sep: float) -> list[float]:
    """Nudge converging labels apart, preserving their true vertical order.

    Direct labels sit at each series' last value, so any two series that
    converge print on top of each other — a close and a 52-week MA both ending
    near $32.50 overprint exactly. Walk the labels in ascending order and push
    each one up until it clears its neighbor.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = list(values)
    for k in range(1, len(order)):
        prev, cur = order[k - 1], order[k]
        if out[cur] - out[prev] < min_sep:
            out[cur] = out[prev] + min_sep
    return out


def _direct_label(fig, x, y, text: str, color: str, *, xref: str, yref: str) -> None:
    """Place a series label in the right margin at the series' last value.

    Direct labels replace the legend (§17.2). Four series in a legend box need a
    color swatch to carry identity, which fails outright for the low-contrast
    slots; a label at the end of the line carries identity in text and color
    together.
    """
    fig.add_annotation(
        x=x, y=y, text=text, xref=xref, yref=yref,
        xanchor="left", yanchor="middle", xshift=6, showarrow=False,
        font=dict(family=FONT_FAMILY, size=10, color=color))


def _iso(index) -> list[str]:
    """A DatetimeIndex as ISO date strings.

    Not cosmetic: kaleido 1.3 serializes the figure with orjson, whose default
    refuses `pandas.Timestamp` (a `datetime` subclass) — and it fails at
    PNG-write time, long after the figure itself looked correct.
    """
    return [ts.date().isoformat() for ts in index]


def build_figure(symbol: str, wk):
    """The three-panel figure. Pure function of the weekly frame."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.62, 0.16, 0.22], vertical_spacing=0.035)
    x = _iso(wk.index)
    last_x, first_x = x[-1], x[0]

    def last_of(col: str) -> float | None:
        if col not in wk.columns:
            return None
        series = wk[col].dropna()
        return None if series.empty else float(series.iloc[-1])

    # --- row 1: weekly candles + moving averages ---------------------------
    fig.add_trace(go.Candlestick(
        x=x, open=wk["Open"], high=wk["High"], low=wk["Low"], close=wk["Close"],
        name=symbol,
        increasing=dict(fillcolor="rgba(0,0,0,0)", line=dict(color=UP, width=1)),
        decreasing=dict(fillcolor=DOWN, line=dict(color=DOWN, width=1)),
    ), row=1, col=1)

    for col, color in ((f"MA{MA_SHORT}", S1_MA13), (f"MA{MA_LONG}", S2_MA52)):
        fig.add_trace(go.Scatter(x=x, y=wk[col], name=col,
                                 line=dict(color=color, width=1.75)),
                      row=1, col=1)

    # --- row 2: volume ------------------------------------------------------
    # One color, deliberately (§17.2). Coloring each bar by the week's direction
    # re-encodes what the panel above already shows, and at this bar width it
    # reads as confetti.
    fig.add_trace(go.Bar(x=x, y=wk["Volume"], name="Volume",
                         marker_color=S3_VOLUME, opacity=0.45),
                  row=2, col=1)

    # --- row 3: relative strength ------------------------------------------
    has_rs = "RS" in wk.columns and bool(wk["RS"].notna().any())
    if has_rs:
        fig.add_trace(go.Scatter(x=x, y=wk["RS"], name="RS",
                                 line=dict(color=S4_RS, width=1.75)),
                      row=3, col=1)
        # Relative strength is meaningless without parity drawn: 1.0 is the line
        # between outperforming and lagging the index.
        fig.add_hline(y=1.0, line=dict(color=RULE, width=1), row=3, col=1)
        fig.add_annotation(
            x=first_x, y=1.0, text=PARITY_ANNOTATION, xref="x3", yref="y3",
            xanchor="left",
            # Below the rule: an indexed RS series starts at 1.0 and moves up,
            # so the space above parity is where the curve lives.
            yanchor="top", yshift=-3, showarrow=False,
            font=dict(family=FONT_FAMILY, size=9, color=MUTED))

    # --- direct labels in place of a legend --------------------------------
    labels = [(last_of("Close"), symbol, INK),
              (last_of(f"MA{MA_SHORT}"), f"MA{MA_SHORT}", S1_MA13),
              (last_of(f"MA{MA_LONG}"), f"MA{MA_LONG}", S2_MA52)]
    labels = [(v, t, c) for v, t, c in labels if v is not None]
    if labels:
        # A ~13px minimum gap expressed in price units: row 1 owns 0.62 of the
        # plot area, so one pixel is that share of the visible price range.
        cols = [wk[c].dropna() for c in ("Close", f"MA{MA_SHORT}", f"MA{MA_LONG}")
                if c in wk.columns]
        cols = [s for s in cols if not s.empty]
        lo = min(float(s.min()) for s in cols)
        hi = max(float(s.max()) for s in cols)
        panel_px = (PRICE_HEIGHT - 8 - 28) * 0.62
        min_sep = 13.0 / panel_px * (hi - lo) * 1.1 if hi > lo else 0.0
        for y, (_, text, color) in zip(
                spread_label_positions([v for v, _, _ in labels], min_sep), labels):
            _direct_label(fig, last_x, y, text, color, xref="x", yref="y")

    if has_rs:
        rs_last = last_of("RS")
        if rs_last is not None:
            _direct_label(fig, last_x, rs_last, "RS", S4_RS,
                          xref="x3", yref="y3")

    apply_base_layout(fig, height=PRICE_HEIGHT)
    fig.update_layout(bargap=0.15)
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(tickprefix="$", row=1, col=1)
    fig.update_yaxes(title_text="Volume", title_font=SMALL_AXIS_TITLE_FONT,
                     row=2, col=1)
    if has_rs:
        fig.update_yaxes(title_text=RS_AXIS_TITLE,
                         title_font=SMALL_AXIS_TITLE_FONT, row=3, col=1)
    return fig


def _caption(wk, technical: dict | None, as_of: str) -> str:
    """Provider and as-of, plus the trend note when the indicators are there.

    §16.2 requires every selected chart's caption to carry provider and as-of
    from bronze metadata; building it here means the chartbook subagent never
    has to invent one.
    """
    last = float(wk["Close"].iloc[-1])
    parts = [f"Weekly candles with 13- and 52-week moving averages; "
             f"last close ${last:,.2f} ({as_of}).",
             "Source: Yahoo Finance."]
    if technical:
        signals = (technical.get("trend_signals") or {})
        trend = signals.get("trend")
        rsi = (technical.get("indicators") or {}).get("rsi")
        if trend or rsi is not None:
            note = "Technical read: " + ", ".join(
                p for p in (trend, f"RSI {rsi}" if rsi is not None else None) if p)
            parts.insert(1, note + ".")
    return " ".join(parts)


def render_price_weekly(ticker_dir: Path, *, write_png: bool = True,
                        now: datetime | None = None) -> ChartResult | None:
    """Render `price_weekly`, or `None` when the price series is unusable.

    Total by construction (§16.1): a missing artifact, a malformed one, or an
    empty series all degrade to "no chart" rather than raising into the caller's
    render loop.
    """
    prices = read_prices(ticker_dir)
    if not prices:
        return None
    try:
        wk = weekly_frame(prices)
    except (KeyError, ValueError, TypeError):
        return None
    if wk.empty:
        return None

    technical = _read_data(
        ticker_dir / "structured" / "technical_indicators_computed.json")
    data_sources = ["prices_yahoo"]
    if technical:
        data_sources.append("technical_indicators_computed")

    as_of = str(wk.index[-1].date())
    now = now or datetime.now(timezone.utc)
    recency = (now.date() - date.fromisoformat(as_of)).days

    fig = build_figure(ticker_dir.name.upper(), wk)
    return write_candidate(
        ticker_dir, fig,
        name=CHART_NAME,
        title=f"{ticker_dir.name.upper()} weekly price, volume and relative strength",
        data_sources=data_sources,
        auto_caption=_caption(wk, technical, as_of),
        salience={
            "recency_days": recency,
            "coverage": round(len(wk) / MAX_WEEKS, 2),
            "variance_note": f"{len(wk)} weekly bars through {as_of}",
        },
        height=PRICE_HEIGHT,
        write_png=write_png)
