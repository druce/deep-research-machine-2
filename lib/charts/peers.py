#!/usr/bin/env python3
"""Peer comparison exhibits (spec §16.1, §13.6).

Two charts: a growth-versus-margin scatter that places the subject among its
comparables, and a multiples bar that says what the market pays for each.

Where the numbers come from matters. §13.6 is explicit that peer-selection files
are silver — `peers_selected.json` records WHY those five were chosen and is
lineage, not evidence — so the metrics here are read from each peer's OWN bronze
under `data/<PEER>/structured/`. A peer whose ticker has never been built has no
bronze to read, so it is left out of the chart and named in the caption as a
disclosed gap (§6.4) rather than filled in from the silver candidate table.

That is a real constraint, not a technicality: a comparables exhibit built from
the selection artifact would put numbers on the page whose provenance runs
through a model's ranking rather than through a provider's filing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lib.charts.base import (
    INK, MUTED, S1_MA13, S4_RS, SMALL_AXIS_TITLE_FONT, ChartResult, FONT_FAMILY,
    apply_base_layout, write_candidate)
from lib.charts.common import number, percent, read_artifact, read_derived, read_meta

PEER_HEIGHT = 460

# The subject is drawn in body ink and every peer in one muted tone: the reader
# is looking for "where do we sit", and coloring peers individually would invite
# a comparison between them that the exhibit is not making.
SUBJECT_COLOR = INK
PEER_COLOR = S1_MA13

MIN_PEERS = 2       # fewer than two comparables is not a comparison


def _peer_dir(ticker_dir: Path, symbol: str) -> Path:
    return ticker_dir.parent / symbol.upper()


def _metrics(ticker_dir: Path, symbol: str) -> dict | None:
    """One company's ratio block from its own bronze, or `None`."""
    ratios = read_artifact(_peer_dir(ticker_dir, symbol), "key_ratios_computed")
    if not ratios:
        return None
    valuation = ratios.get("valuation") or {}
    highlights = ratios.get("highlights") or {}
    profitability = ratios.get("profitability") or {}
    return {
        "symbol": symbol.upper(),
        "market_cap": number(highlights.get("market_cap")),
        "revenue_ttm": number(highlights.get("revenue_ttm")),
        "revenue_growth": _as_percent(number(highlights.get("revenue_growth_yoy"))),
        "operating_margin": _as_percent(number(profitability.get("operating_margin"))),
        "forward_pe": number(valuation.get("forward_pe")),
        "ev_to_revenue": number(valuation.get("ev_to_revenue")),
    }


def _as_percent(value: float | None) -> float | None:
    """Yahoo reports these ratios as fractions; the axis is labeled in percent."""
    return None if value is None else value * 100


def _cohort(ticker_dir: Path) -> tuple[list[dict], list[str]] | None:
    """`(rows, missing_symbols)` for the subject plus every peer with bronze."""
    selected = read_derived(ticker_dir, "peers", "peers_selected")
    if not selected:
        return None
    symbols = [str(row.get("symbol")) for row in (selected.get("peers") or [])
               if row.get("symbol")]
    if not symbols:
        return None

    subject = _metrics(ticker_dir, ticker_dir.name)
    if subject is None:
        return None
    subject["is_subject"] = True

    rows, missing = [subject], []
    for symbol in symbols:
        found = _metrics(ticker_dir, symbol)
        if found is None:
            missing.append(symbol.upper())
        else:
            found["is_subject"] = False
            rows.append(found)
    return rows, missing


def _sources(rows: list[dict]) -> list[str]:
    """One id per company whose bronze was actually read, plus the lineage."""
    return ["peers_selected"] + [
        "key_ratios_computed" if row["is_subject"]
        else f"{row['symbol']}:key_ratios_computed" for row in rows]


def _gap_clause(missing: list[str]) -> str | None:
    if not missing:
        return None
    return (f"No bronze financials on file for {', '.join(missing)} — "
            f"excluded rather than estimated")


def _caption(body: list[str], missing: list[str], as_of: str) -> str:
    parts = list(body)
    clause = _gap_clause(missing)
    if clause:
        parts.append(clause + ".")
    parts.append(f"Source: Yahoo Finance, as of {as_of}.")
    return " ".join(parts)


def _as_of(ticker_dir: Path) -> str:
    return str((read_meta(ticker_dir, "key_ratios_computed") or {}).get("as_of")
               or datetime.now(timezone.utc).date().isoformat())


def render_peer_scatter(ticker_dir: Path, *,
                        write_png: bool = True) -> ChartResult | None:
    """Revenue growth against operating margin, sized by market capitalization."""
    import plotly.graph_objects as go

    cohort = _cohort(ticker_dir)
    if cohort is None:
        return None
    rows, missing = cohort
    plotted = [r for r in rows if r["revenue_growth"] is not None
               and r["operating_margin"] is not None]
    if len(plotted) < MIN_PEERS:
        return None

    caps = [r["market_cap"] or 0 for r in plotted]
    largest = max(caps) or 1
    fig = go.Figure()
    for row in plotted:
        subject = row["is_subject"]
        fig.add_trace(go.Scatter(
            x=[row["revenue_growth"]], y=[row["operating_margin"]],
            mode="markers+text", name=row["symbol"],
            text=[row["symbol"]], textposition="top center",
            textfont=dict(family=FONT_FAMILY, size=10,
                          color=SUBJECT_COLOR if subject else MUTED),
            marker=dict(
                # Area, not diameter, carries the magnitude: sizing by diameter
                # would make a company twice the size look four times as big.
                size=14 + 26 * ((row["market_cap"] or 0) / largest) ** 0.5,
                color=SUBJECT_COLOR if subject else PEER_COLOR,
                opacity=1.0 if subject else 0.55,
                line=dict(color=SUBJECT_COLOR if subject else PEER_COLOR,
                          width=1))))

    apply_base_layout(fig, height=PEER_HEIGHT)
    fig.update_xaxes(title_text="Revenue growth, YoY (%)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT)
    fig.update_yaxes(title_text="Operating margin (%)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT)

    subject = next(r for r in plotted if r["is_subject"])
    return write_candidate(
        ticker_dir, fig,
        name="peer_scatter",
        title=f"{ticker_dir.name.upper()} against its peer set: growth and margin",
        data_sources=_sources(plotted),
        auto_caption=_caption(
            [f"{subject['symbol']} grows {percent(subject['revenue_growth'])} at a "
             f"{percent(subject['operating_margin'])} operating margin against "
             f"{len(plotted) - 1} comparables; marker area is market "
             f"capitalization."],
            missing, _as_of(ticker_dir)),
        salience={"recency_days": 0,
                  "coverage": round((len(plotted) - 1)
                                    / max(len(rows) - 1 + len(missing), 1), 2),
                  "variance_note": f"{len(plotted)} companies plotted"},
        height=PEER_HEIGHT, write_png=write_png)


def render_peer_multiples(ticker_dir: Path, *,
                          write_png: bool = True) -> ChartResult | None:
    """Forward P/E across the peer set, subject first and in body ink."""
    import plotly.graph_objects as go

    cohort = _cohort(ticker_dir)
    if cohort is None:
        return None
    rows, missing = cohort
    plotted = [r for r in rows if r["forward_pe"] is not None]
    if len(plotted) < MIN_PEERS:
        return None

    plotted.sort(key=lambda r: (not r["is_subject"], -(r["forward_pe"] or 0)))
    symbols = [r["symbol"] for r in plotted]
    values = [r["forward_pe"] for r in plotted]
    colors = [SUBJECT_COLOR if r["is_subject"] else PEER_COLOR for r in plotted]

    fig = go.Figure(go.Bar(x=symbols, y=values, marker_color=colors,
                           text=[f"{v:.1f}x" for v in values],
                           textposition="outside",
                           textfont=dict(family=FONT_FAMILY, size=10, color=INK)))
    peers_only = [v for r, v in zip(plotted, values) if not r["is_subject"]]
    median = sorted(peers_only)[len(peers_only) // 2]
    fig.add_hline(y=median, line=dict(color=S4_RS, width=1.5, dash="dot"))
    fig.add_annotation(x=symbols[-1], y=median, text=f"Peer median {median:.1f}x",
                       xanchor="right", yanchor="bottom", showarrow=False,
                       font=dict(family=FONT_FAMILY, size=10, color=S4_RS))

    apply_base_layout(fig, height=PEER_HEIGHT)
    fig.update_yaxes(title_text="Forward price / earnings (x)", ticksuffix="x",
                     title_font=SMALL_AXIS_TITLE_FONT)

    subject = next(r for r in plotted if r["is_subject"])
    return write_candidate(
        ticker_dir, fig,
        name="peer_multiples",
        title=f"{ticker_dir.name.upper()} forward multiple against its peer set",
        data_sources=_sources(plotted),
        auto_caption=_caption(
            [f"{subject['symbol']} trades at {subject['forward_pe']:.1f}x forward "
             f"earnings against a {median:.1f}x peer median across "
             f"{len(peers_only)} comparables."],
            missing, _as_of(ticker_dir)),
        salience={"recency_days": 0,
                  "coverage": round(len(peers_only)
                                    / max(len(peers_only) + len(missing), 1), 2),
                  "variance_note": f"subject {subject['forward_pe']:.1f}x vs "
                                   f"median {median:.1f}x"},
        height=PEER_HEIGHT, write_png=write_png)
