#!/usr/bin/env python3
"""Conclusion-dependent exhibits (spec §16.1, §16.3, §16.4).

These render in the second pass, `sra.py charts T --verdict`, because they read
`verdict.json` — which does not exist until the polish chain has written it. A
renderer here is still a pure function of persisted artifacts; the verdict is
simply one more persisted artifact, and §16.3 requires it be declared as an
input dependency in the manifest. That declaration is what lets a reader tell
"the model said $250" from "Yahoo said $250".

**On the DCF exhibit.** §16.1 lists it as an example of a verdict-dependent
chart, but also puts "DCF sensitivity" in Tier 2 — no bronze producer — and
§15.3's `verdict.json` carries a fair value and a `valuation_method` string, not
a discount rate, a terminal growth rate, or a cash-flow forecast. There is
nothing to plot that would not be invented here, so it is deliberately not
built. When a producer supplies DCF assumptions, this is where the exhibit goes.
"""

from __future__ import annotations

from pathlib import Path

from lib.charts.base import (
    CATEGORICAL, INK, MUTED, RULE, S4_RS, SMALL_AXIS_TITLE_FONT, ChartResult,
    FONT_FAMILY, apply_base_layout, load_verdict, write_candidate)
from lib.charts.common import number, read_artifact, read_meta

VERDICT_HEIGHT = 440

# A band needs both ends to be a band. One-sided ranges are dropped rather than
# drawn from zero, which would make every method look like it spans the whole
# axis.
MIN_BANDS = 1


def _price_target_band(ticker_dir: Path) -> tuple[str, float, float, str] | None:
    data = read_artifact(ticker_dir, "price_targets_yahoo")
    targets = (data or {}).get("price_targets") or {}
    low, high = number(targets.get("low")), number(targets.get("high"))
    if low is None or high is None or high <= low:
        return None
    source = str((read_meta(ticker_dir, "price_targets_yahoo") or {})
                 .get("source") or "Yahoo Finance")
    return ("Analyst targets", low, high, source)


def _fifty_two_week_band(ticker_dir: Path) -> tuple[str, float, float, str] | None:
    data = read_artifact(ticker_dir, "prices_yahoo")
    daily = (data or {}).get("daily") or {}
    closes = daily.get("close") or []
    if len(closes) < 2:
        return None
    window = [c for c in closes[-252:] if number(c) is not None]
    if not window:
        return None
    low, high = min(window), max(window)
    if high <= low:
        return None
    return ("52-week range", float(low), float(high), "Yahoo Finance")


def _peer_multiple_band(ticker_dir: Path) -> tuple[str, float, float, str] | None:
    """Forward EPS times the peer multiple spread — what the cohort would pay.

    Deliberately the peers' OWN range rather than a point estimate: a single
    median multiplied out reads as a precision the comparison does not have.
    """
    from lib.charts.peers import _cohort

    ratios = read_artifact(ticker_dir, "key_ratios_computed")
    eps = number(((ratios or {}).get("per_share") or {}).get("eps_forward"))
    if not eps or eps <= 0:
        return None
    cohort = _cohort(ticker_dir)
    if cohort is None:
        return None
    rows, _ = cohort
    multiples = [r["forward_pe"] for r in rows
                 if not r["is_subject"] and r["forward_pe"]]
    if len(multiples) < 2:
        return None
    return ("Peer multiple", min(multiples) * eps, max(multiples) * eps,
            "Yahoo Finance")


def render_valuation_football_field(ticker_dir: Path, *, write_png: bool = True
                                    ) -> ChartResult | None:
    """The fair value against every valuation band the evidence supports.

    Each bar is a range one method produces; the verdict's fair value and the
    current price are vertical rules across all of them. The exhibit's whole
    job is to show whether the conclusion sits inside or outside what the
    mechanical evidence would pay.
    """
    import plotly.graph_objects as go

    verdict = load_verdict(ticker_dir)
    if not verdict:
        return None
    fair_value = number(verdict.get("fair_value"))
    if not fair_value:
        return None

    bands = [b for b in (_price_target_band(ticker_dir),
                         _fifty_two_week_band(ticker_dir),
                         _peer_multiple_band(ticker_dir)) if b is not None]
    if len(bands) < MIN_BANDS:
        return None

    current = number(verdict.get("current_price"))
    if current is None:
        prices = read_artifact(ticker_dir, "prices_yahoo") or {}
        closes = (prices.get("daily") or {}).get("close") or []
        current = number(closes[-1]) if closes else None

    labels = [b[0] for b in bands]
    fig = go.Figure()
    for (label, low, high, _), color in zip(bands, CATEGORICAL):
        fig.add_trace(go.Bar(
            x=[high - low], y=[label], base=[low], orientation="h",
            marker_color=color, opacity=0.55, width=0.45,
            text=[f"${low:,.0f} – ${high:,.0f}"], textposition="outside",
            textfont=dict(family=FONT_FAMILY, size=10, color=INK)))

    fig.add_vline(x=fair_value, line=dict(color=S4_RS, width=2))
    fig.add_annotation(x=fair_value, y=1, yref="paper",
                       text=f"Fair value ${fair_value:,.0f}", xanchor="left",
                       yanchor="top", xshift=5, showarrow=False,
                       font=dict(family=FONT_FAMILY, size=10, color=S4_RS))
    if current:
        fig.add_vline(x=current, line=dict(color=MUTED, width=1.5, dash="dot"))
        fig.add_annotation(x=current, y=0, yref="paper",
                           text=f"Current ${current:,.0f}", xanchor="right",
                           yanchor="bottom", xshift=-5, showarrow=False,
                           font=dict(family=FONT_FAMILY, size=10, color=MUTED))

    apply_base_layout(fig, height=VERDICT_HEIGHT)
    # A horizontal bar chart's value axis is x, so the grid has to move with it:
    # §17.4's "horizontal gridlines only" is about gridlines running ACROSS the
    # value axis, not about which letter the axis happens to be called.
    fig.update_yaxes(showgrid=False, categoryorder="array",
                     categoryarray=list(reversed(labels)))
    # Room on the right for the widest band's outside label, which plotly will
    # otherwise draw past the plot edge and clip. Padding the range is better
    # than moving the label inside: on a 0.55-opacity fill the text loses
    # contrast, and a narrow band cannot hold it at all.
    lows = [b[1] for b in bands] + [v for v in (fair_value, current) if v]
    highs = [b[2] for b in bands] + [v for v in (fair_value, current) if v]
    span = max(highs) - min(lows)
    fig.update_xaxes(showgrid=True, gridcolor=RULE, tickprefix="$",
                     range=[min(lows) - span * 0.05, max(highs) + span * 0.22],
                     title_text="Share price (US$)",
                     title_font=SMALL_AXIS_TITLE_FONT)

    implied = number(verdict.get("implied_return_pct"))
    method = str(verdict.get("valuation_method") or "").strip()
    body = [f"Fair value of ${fair_value:,.0f}"]
    if method:
        body.append(f"by {method}")
    if current:
        body.append(f"against ${current:,.0f} today")
    if implied is not None:
        body.append(f"({implied:+.1f}% implied)")
    caption = (" ".join(body) + ". Bands: "
               + "; ".join(f"{label} ${low:,.0f}-${high:,.0f}"
                           for label, low, high, _ in bands)
               + f". Sources: {', '.join(sorted({b[3] for b in bands}))}; "
                 f"fair value from verdict.json.")

    return write_candidate(
        ticker_dir, fig,
        name="valuation_football_field",
        title=f"{ticker_dir.name.upper()} valuation football field",
        # §16.3: a verdict-dependent exhibit MUST declare `verdict` as an input.
        data_sources=["verdict"] + _band_sources(labels),
        auto_caption=caption,
        salience={
            "recency_days": 0,
            "coverage": round(len(bands) / 3, 2),
            "variance_note": f"{len(bands)} valuation bands; fair value "
                             f"{'inside' if _inside(fair_value, bands) else 'outside'} "
                             f"every band",
        },
        height=VERDICT_HEIGHT, write_png=write_png)


def _band_sources(labels: list[str]) -> list[str]:
    """The bronze ids behind whichever bands were actually drawn."""
    mapping = {
        "Analyst targets": ["price_targets_yahoo"],
        "52-week range": ["prices_yahoo"],
        "Peer multiple": ["key_ratios_computed", "peers_selected"],
    }
    out: list[str] = []
    for label in labels:
        for artifact_id in mapping.get(label, []):
            if artifact_id not in out:
                out.append(artifact_id)
    return out


def _inside(fair_value: float, bands: list[tuple[str, float, float, str]]) -> bool:
    """Does the conclusion sit inside every band the evidence drew?

    A salience signal, not a judgment: a fair value outside every mechanical
    range is exactly the exhibit a reader should be shown, whether the argument
    for it is good or bad.
    """
    return all(low <= fair_value <= high for _, low, high, _ in bands)
