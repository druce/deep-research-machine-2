#!/usr/bin/env python3
"""Fundamental exhibits: revenue, margins, cash conversion, valuation history.

Four Tier-1 charts (§16.1), all pure functions of the stored annual statements
and ratio artifacts. Three rules from the spec shape every one of them:

- **§6.4 — no interpolation.** A period a provider did not report is a break in
  the line (`connectgaps=False`) and a clause in the caption, never a segment
  drawn through numbers nobody published.
- **§17.4 — units visible.** Every axis carries its unit, because a chart read
  out of the report body has no surrounding prose to supply one.
- **§17.2's no-secondary-axis rule, applied generally.** Where a figure pairs a
  level with a rate — revenue with growth, free cash flow with conversion — it
  gets two panels rather than two scales on one frame. A reader who cannot tell
  which curve owns which axis is reading a decoration.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from lib.charts.base import (
    CATEGORICAL, MUTED, RULE, S1_MA13, S2_MA52, SMALL_AXIS_TITLE_FONT,
    ChartResult, FONT_FAMILY, apply_base_layout, write_candidate)
from lib.charts.common import (
    gap_note, money, number, percent, periods_ascending, read_artifact, read_meta,
    statement_series)

FUNDAMENTAL_HEIGHT = 460

REVENUE_NAMES = ("Total Revenue", "TotalRevenue")
GROSS_NAMES = ("Gross Profit", "GrossProfit")
OPERATING_NAMES = ("Operating Income", "OperatingIncome", "EBIT")
NET_NAMES = ("Net Income", "NetIncome", "Net Income Common Stockholders")
OCF_NAMES = ("Operating Cash Flow", "OperatingCashFlow",
             "Total Cash From Operating Activities")
CAPEX_NAMES = ("Capital Expenditure", "CapitalExpenditure", "Capital Expenditures")
FCF_NAMES = ("Free Cash Flow", "FreeCashFlow")
EPS_NAMES = ("Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS")


def _periods_are_categories(fig) -> None:
    """Treat fiscal period ends as categories, not as points on a time axis.

    Four annual bars on a date axis are four bars sized to the gaps between
    them: plotly widens each to fill its slot, the labels land between the bars
    rather than under them, and the reader is invited to read a duration off an
    exhibit that has none. Fiscal years are ordered labels, so the axis says so.
    """
    fig.update_xaxes(type="category")


def _label(fig, x, y, text: str, color: str, *, xref: str = "x", yref: str = "y",
           size: int = 10) -> None:
    fig.add_annotation(x=x, y=y, text=text, xref=xref, yref=yref, xanchor="left",
                       yanchor="middle", xshift=6, showarrow=False,
                       font=dict(family=FONT_FAMILY, size=size, color=color))


def _caption(parts: list[str], gaps: list[str | None], source: str) -> str:
    """Body sentences, then the disclosed gaps, then the provider (§16.2)."""
    disclosed = [g for g in gaps if g]
    if disclosed:
        parts.append("Gaps: " + "; ".join(disclosed) + ".")
    parts.append(f"Source: {source}.")
    return " ".join(parts)


def _provider(ticker_dir: Path, artifact_id: str) -> tuple[str, str]:
    """`(source, as_of)` from an artifact's `_meta`, for the caption."""
    meta = read_meta(ticker_dir, artifact_id) or {}
    return str(meta.get("source") or "unknown"), str(meta.get("as_of") or "")


def _salience(periods: list[str], covered: int, note: str,
              now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        recency = (now.date() - date.fromisoformat(periods[-1])).days
    except (IndexError, ValueError):
        recency = 0
    return {"recency_days": recency,
            "coverage": round(covered / len(periods), 2) if periods else 0.0,
            "variance_note": note}


# --- revenue and growth -----------------------------------------------------

def render_revenue_growth(ticker_dir: Path, *,
                          write_png: bool = True) -> ChartResult | None:
    """Revenue bars over the reported years, with year-on-year growth beneath."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    income = read_artifact(ticker_dir, "income_statement_yahoo")
    if not income:
        return None
    revenue = statement_series(income, *REVENUE_NAMES)
    periods = [p for p, v in revenue.items() if v is not None]
    if len(periods) < 2:
        return None

    values = [revenue[p] for p in periods]
    growth: list[float | None] = [None]
    for prev, cur in zip(values, values[1:]):
        growth.append((cur / prev - 1) * 100 if prev else None)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.66, 0.34], vertical_spacing=0.06)
    fig.add_trace(go.Bar(x=periods, y=values, marker_color=S1_MA13,
                         name="Revenue"), row=1, col=1)
    fig.add_trace(go.Scatter(x=periods, y=growth, mode="lines+markers",
                             connectgaps=False, name="YoY growth",
                             line=dict(color=S2_MA52, width=1.75)), row=2, col=1)
    # Zero on a growth panel is analytically required: it is the line between
    # growth and contraction, which §17.4's "no zero line" default does not mean
    # to suppress.
    fig.add_hline(y=0, line=dict(color=RULE, width=1), row=2, col=1)

    apply_base_layout(fig, height=FUNDAMENTAL_HEIGHT)
    _periods_are_categories(fig)
    fig.update_yaxes(title_text="Revenue (US$)", title_font=SMALL_AXIS_TITLE_FONT,
                     row=1, col=1)
    fig.update_yaxes(title_text="YoY growth (%)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT, row=2, col=1)
    # No direct label on the final bar: §17.2's rule replaces a LEGEND, and a
    # single-series bar chart has no identity to carry — the axis states the
    # magnitude and the caption states the latest figure.

    source, _ = _provider(ticker_dir, "income_statement_yahoo")
    latest = growth[-1]
    return write_candidate(
        ticker_dir, fig,
        name="revenue_growth",
        title=f"{ticker_dir.name.upper()} revenue and year-on-year growth",
        data_sources=["income_statement_yahoo"],
        auto_caption=_caption(
            [f"Annual revenue of {money(values[-1])} for the period ending "
             f"{periods[-1]}, {percent(latest)} year on year."],
            [gap_note(revenue, "Revenue")], source),
        salience=_salience(list(revenue), len(periods),
                           f"{len(periods)} reported years through {periods[-1]}"),
        height=FUNDAMENTAL_HEIGHT, write_png=write_png)


# --- margin trends ----------------------------------------------------------

def render_margin_trends(ticker_dir: Path, *,
                         write_png: bool = True) -> ChartResult | None:
    """Gross, operating and net margin over the reported years."""
    import plotly.graph_objects as go

    income = read_artifact(ticker_dir, "income_statement_yahoo")
    if not income:
        return None
    revenue = statement_series(income, *REVENUE_NAMES)
    lines = {
        "Gross": statement_series(income, *GROSS_NAMES),
        "Operating": statement_series(income, *OPERATING_NAMES),
        "Net": statement_series(income, *NET_NAMES),
    }
    periods = periods_ascending(income)
    margins = {
        name: [None if not revenue.get(p) or series.get(p) is None
               else series[p] / revenue[p] * 100 for p in periods]
        for name, series in lines.items()
    }
    if not any(any(v is not None for v in series) for series in margins.values()):
        return None

    fig = go.Figure()
    # Three categoricals, and the third (#1baf7a) is below the legend-swatch
    # contrast threshold — which costs nothing here, because §17.2's direct
    # labeling replaces the legend anyway.
    for (name, series), color in zip(margins.items(), CATEGORICAL):
        fig.add_trace(go.Scatter(x=periods, y=series, mode="lines+markers",
                                 connectgaps=False, name=name,
                                 line=dict(color=color, width=1.75)))
        final = next((v for v in reversed(series) if v is not None), None)
        if final is not None:
            _label(fig, periods[-1], final, f"{name} {final:.1f}%", color)

    apply_base_layout(fig, height=FUNDAMENTAL_HEIGHT)
    _periods_are_categories(fig)
    fig.update_yaxes(title_text="Margin (% of revenue)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT)

    source, _ = _provider(ticker_dir, "income_statement_yahoo")
    covered = sum(1 for p in periods if revenue.get(p) is not None)
    gaps = [gap_note(lines[name], f"{name} profit") for name in lines]
    latest = {name: next((v for v in reversed(series) if v is not None), None)
              for name, series in margins.items()}
    return write_candidate(
        ticker_dir, fig,
        name="margin_trends",
        title=f"{ticker_dir.name.upper()} margin trends",
        data_sources=["income_statement_yahoo"],
        auto_caption=_caption(
            ["Margins as a share of revenue: "
             + ", ".join(f"{k.lower()} {percent(v)}" for k, v in latest.items())
             + f" for the period ending {periods[-1]}."],
            gaps, source),
        salience=_salience(periods, covered,
                           f"{len(periods)} reported years through {periods[-1]}"),
        height=FUNDAMENTAL_HEIGHT, write_png=write_png)


# --- free cash flow and conversion -----------------------------------------

def render_fcf_conversion(ticker_dir: Path, *,
                          write_png: bool = True) -> ChartResult | None:
    """Free cash flow bars, with conversion against net income beneath.

    Free cash flow is taken as reported when the provider supplies it, and
    otherwise as operating cash flow plus capital expenditure — capex arrives
    negative from this provider, so it adds rather than subtracts. Both are one
    provider's numbers (§6.4: one provider per computed figure).
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    cashflow = read_artifact(ticker_dir, "cashflow_yahoo")
    income = read_artifact(ticker_dir, "income_statement_yahoo")
    if not cashflow or not income:
        return None

    reported = statement_series(cashflow, *FCF_NAMES)
    ocf = statement_series(cashflow, *OCF_NAMES)
    capex = statement_series(cashflow, *CAPEX_NAMES)
    net = statement_series(income, *NET_NAMES)

    periods = periods_ascending(cashflow)
    fcf: dict[str, float | None] = {}
    for period in periods:
        value = reported.get(period)
        if value is None and ocf.get(period) is not None \
                and capex.get(period) is not None:
            value = ocf[period] + capex[period]
        fcf[period] = value
    usable = [p for p in periods if fcf[p] is not None]
    if not usable:
        return None

    conversion = [None if not net.get(p) or fcf[p] is None
                  else fcf[p] / net[p] * 100 for p in periods]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.66, 0.34], vertical_spacing=0.06)
    fig.add_trace(go.Bar(x=periods, y=[fcf[p] for p in periods],
                         marker_color=S1_MA13, name="Free cash flow"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=periods, y=conversion, mode="lines+markers",
                             connectgaps=False, name="Conversion",
                             line=dict(color=S2_MA52, width=1.75)), row=2, col=1)
    # 100% is the analytically meaningful line here: cash earnings equal to
    # accounting earnings.
    fig.add_hline(y=100, line=dict(color=RULE, width=1), row=2, col=1)
    fig.add_annotation(x=periods[0], y=100, text="100% = net income", xref="x2",
                       yref="y2", xanchor="left", yanchor="bottom", showarrow=False,
                       font=dict(family=FONT_FAMILY, size=9, color=MUTED))

    apply_base_layout(fig, height=FUNDAMENTAL_HEIGHT)
    _periods_are_categories(fig)
    fig.update_yaxes(title_text="Free cash flow (US$)",
                     title_font=SMALL_AXIS_TITLE_FONT, row=1, col=1)
    fig.update_yaxes(title_text="Conversion (% of net income)", ticksuffix="%",
                     title_font=SMALL_AXIS_TITLE_FONT, row=2, col=1)

    source, _ = _provider(ticker_dir, "cashflow_yahoo")
    latest = usable[-1]
    return write_candidate(
        ticker_dir, fig,
        name="fcf_conversion",
        title=f"{ticker_dir.name.upper()} free cash flow and conversion",
        data_sources=["cashflow_yahoo", "income_statement_yahoo"],
        auto_caption=_caption(
            [f"Free cash flow of {money(fcf[latest])} for the period ending "
             f"{latest}, {percent(conversion[periods.index(latest)])} of net "
             f"income."],
            [gap_note(fcf, "Free cash flow"), gap_note(net, "Net income")],
            source),
        salience=_salience(periods, len(usable),
                           f"{len(usable)} of {len(periods)} years with cash-flow data"),
        height=FUNDAMENTAL_HEIGHT, write_png=write_png)


# --- forward multiple vs history -------------------------------------------

def render_forward_multiple(ticker_dir: Path, *,
                            write_png: bool = True) -> ChartResult | None:
    """Today's forward P/E against the trailing P/E actually paid each year.

    The historical points are computed, not fetched: the closing price at each
    fiscal period end divided by that year's diluted EPS. §16.1 notes that
    estimate-revision history is unavailable because the estimate artifacts
    overwrite in place, so this is the honest version of "multiple vs history" —
    one forward point, and the trailing multiples the market actually paid.
    """
    import plotly.graph_objects as go

    income = read_artifact(ticker_dir, "income_statement_yahoo")
    prices = read_artifact(ticker_dir, "prices_yahoo")
    ratios = read_artifact(ticker_dir, "key_ratios_computed")
    if not income or not prices or not ratios:
        return None

    daily = prices.get("daily") or {}
    dates = daily.get("dates") or []
    closes = daily.get("close") or []
    if not dates or len(dates) != len(closes):
        return None

    eps = statement_series(income, *EPS_NAMES)
    periods, multiples = [], []
    for period in periods_ascending(income):
        value = eps.get(period)
        if not value or value <= 0:
            continue
        close = next((c for d, c in zip(reversed(dates), reversed(closes))
                      if d <= period), None)
        if close is None:
            continue
        periods.append(period)
        multiples.append(close / value)
    if len(periods) < 2:
        return None

    forward = number((ratios.get("valuation") or {}).get("forward_pe"))

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=periods, y=multiples, mode="lines+markers",
                             connectgaps=False, name="Trailing P/E",
                             line=dict(color=S1_MA13, width=1.75)))
    _label(fig, periods[-1], multiples[-1], f"Trailing {multiples[-1]:.1f}x",
           S1_MA13)
    if forward:
        fig.add_hline(y=forward, line=dict(color=S2_MA52, width=1.5, dash="dot"))
        fig.add_annotation(x=periods[0], y=forward,
                           text=f"Forward {forward:.1f}x", xanchor="left",
                           yanchor="bottom", showarrow=False,
                           font=dict(family=FONT_FAMILY, size=10, color=S2_MA52))

    apply_base_layout(fig, height=FUNDAMENTAL_HEIGHT)
    _periods_are_categories(fig)
    fig.update_yaxes(title_text="Price / earnings (x)", ticksuffix="x",
                     title_font=SMALL_AXIS_TITLE_FONT)

    price_source, _ = _provider(ticker_dir, "prices_yahoo")
    average = sum(multiples) / len(multiples)
    body = [f"Trailing P/E at each fiscal year end, {multiples[-1]:.1f}x most "
            f"recently against a {average:.1f}x average over {len(periods)} years."]
    if forward:
        body.append(f"Current forward P/E is {forward:.1f}x.")
    return write_candidate(
        ticker_dir, fig,
        name="forward_multiple_vs_history",
        title=f"{ticker_dir.name.upper()} forward multiple against trailing history",
        data_sources=["income_statement_yahoo", "prices_yahoo",
                      "key_ratios_computed"],
        auto_caption=_caption(body, [gap_note(eps, "Diluted EPS")], price_source),
        salience=_salience(periods, len(periods),
                           "computed from period-end closes and diluted EPS"),
        height=FUNDAMENTAL_HEIGHT, write_png=write_png)
