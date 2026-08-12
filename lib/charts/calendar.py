#!/usr/bin/env python3
"""The catalyst calendar (spec §16.1).

A dated timeline of what is coming and what just happened: the forward events
Yahoo publishes (next earnings date, ex-dividend, dividend payment) and the
recent reported quarters with their surprise against consensus.

Two decisions worth stating. Surprise is drawn as a bar against zero because
zero is the analytically meaningful line — met consensus — which is exactly the
case §17.4's "no zero line unless analytically required" leaves room for. And
past and future are separated by a "today" rule rather than by color, because
red-and-green here would collide with §17.1's rule that those two carry meaning
only in candles and the Sankey.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from lib.charts.base import (
    INK, MUTED, RULE, S1_MA13, S4_RS, SMALL_AXIS_TITLE_FONT, ChartResult,
    FONT_FAMILY, apply_base_layout, write_candidate)
from lib.charts.common import number, read_artifact, read_meta

CALENDAR_HEIGHT = 420

# Vertical share of the panel between stacked forward-event labels: enough to
# clear a two-line label at size 10 in a 420px frame.
LABEL_STACK_STEP = 0.13

# The forward events Yahoo names, in the order a reader wants them.
FORWARD_KEYS = (
    ("Earnings Date", "Earnings"),
    ("Ex-Dividend Date", "Ex-dividend"),
    ("Dividend Date", "Dividend paid"),
)


def _dates(value: object) -> list[date]:
    """Every ISO-ish date inside a calendar value (Yahoo gives one or a list)."""
    raw = value if isinstance(value, list) else [value]
    out = []
    for item in raw:
        try:
            out.append(date.fromisoformat(str(item)[:10]))
        except (TypeError, ValueError):
            continue
    return out


def _reported(earnings_dates: dict) -> list[tuple[date, float | None]]:
    """`(date, surprise %)` per reported quarter, newest last."""
    rows: list[tuple[date, float | None]] = []
    for key, values in earnings_dates.items():
        try:
            when = date.fromisoformat(str(key)[:10])
        except (TypeError, ValueError):
            continue
        if not isinstance(values, dict):
            continue
        surprise = number(values.get("Surprise(%)"))
        if surprise is None:
            surprise = number(values.get("surprise_percent"))
        rows.append((when, surprise))
    return sorted(rows)


def events(data: dict) -> tuple[list[tuple[date, float | None]], list[tuple[date, str]]]:
    """`(reported quarters, forward events)` from the stored calendar artifact."""
    calendar = data.get("calendar") or {}
    earnings = data.get("earnings_dates") or {}
    reported = _reported(earnings if isinstance(earnings, dict) else {})
    forward: list[tuple[date, str]] = []
    for key, label in FORWARD_KEYS:
        for when in _dates(calendar.get(key)):
            forward.append((when, label))
    return reported, sorted(forward)


def build_figure(reported: list[tuple[date, float | None]],
                 forward: list[tuple[date, str]], today: date):
    """The dated figure. Pure function of the events and today's date."""
    import plotly.graph_objects as go

    fig = go.Figure()

    quantified = [(d, s) for d, s in reported if s is not None]
    if quantified:
        fig.add_trace(go.Bar(
            x=[d.isoformat() for d, _ in quantified],
            y=[s for _, s in quantified], marker_color=S1_MA13, width=1000 * 60 * 60 * 24 * 12,
            name="Surprise",
            text=[f"{s:+.1f}%" for _, s in quantified], textposition="outside",
            textfont=dict(family=FONT_FAMILY, size=10, color=INK)))
        # Zero is "met consensus" — the whole point of the panel.
        fig.add_hline(y=0, line=dict(color=RULE, width=1))

    # Forward events cluster within weeks of each other — an earnings date and
    # an ex-dividend date days apart is the normal case — so their labels are
    # stacked down the panel rather than all pinned to the top, where they would
    # overprint into an unreadable smear.
    for row, (when, label) in enumerate(sorted(forward)):
        fig.add_vline(x=when.isoformat(),
                      line=dict(color=S4_RS, width=1.5, dash="dot"))
        fig.add_annotation(x=when.isoformat(), y=1 - row * LABEL_STACK_STEP,
                           yref="paper", text=f"{label}<br>{when.isoformat()}",
                           xanchor="left", yanchor="top", xshift=4,
                           showarrow=False,
                           font=dict(family=FONT_FAMILY, size=10, color=S4_RS))

    fig.add_vline(x=today.isoformat(), line=dict(color=MUTED, width=1))
    fig.add_annotation(x=today.isoformat(), y=0, yref="paper", text="today",
                       xanchor="right", yanchor="bottom", xshift=-4,
                       showarrow=False,
                       font=dict(family=FONT_FAMILY, size=9, color=MUTED))

    apply_base_layout(fig, height=CALENDAR_HEIGHT)
    fig.update_yaxes(title_text="EPS surprise vs consensus (%)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT)
    return fig


def render_catalyst_calendar(ticker_dir: Path, *, write_png: bool = True,
                             now: datetime | None = None) -> ChartResult | None:
    """Forward events and recent earnings surprises on one dated axis."""
    data = read_artifact(ticker_dir, "events_calendar_yahoo")
    if not data:
        return None
    reported, forward = events(data)
    if not reported and not forward:
        return None

    today = (now or datetime.now(timezone.utc)).date()
    quantified = [(d, s) for d, s in reported if s is not None]
    fig = build_figure(reported, forward, today)

    meta = read_meta(ticker_dir, "events_calendar_yahoo") or {}
    next_event = min((d for d, _ in forward if d >= today), default=None)
    body = []
    if next_event:
        label = next(name for d, name in forward if d == next_event)
        body.append(f"Next dated catalyst: {label.lower()} on "
                    f"{next_event.isoformat()} ({(next_event - today).days} days).")
    if quantified:
        body.append(f"Bars are reported EPS surprise against consensus for the "
                    f"last {len(quantified)} quarters.")
    if reported and not quantified:
        body.append("Surprise against consensus was not reported for any quarter "
                    "on file.")

    return write_candidate(
        ticker_dir, fig,
        name="catalyst_calendar",
        title=f"{ticker_dir.name.upper()} catalyst calendar and earnings surprises",
        data_sources=["events_calendar_yahoo"],
        auto_caption=" ".join(body + [f"Source: {meta.get('source', 'Yahoo Finance')}, "
                                      f"as of {meta.get('as_of', today.isoformat())}."]),
        salience={
            "recency_days": 0 if next_event is None
            else max((next_event - today).days, 0),
            "coverage": round(len(quantified) / len(reported), 2) if reported else 0.0,
            "variance_note": f"{len(forward)} forward events, "
                             f"{len(reported)} reported quarters",
        },
        height=CALENDAR_HEIGHT, write_png=write_png)
