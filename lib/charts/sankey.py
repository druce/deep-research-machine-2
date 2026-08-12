#!/usr/bin/env python3
"""The income-statement Sankey (spec §16.1, §17.3).

Color is **semantic, not categorical**. An income statement tells one story —
what keeps flowing right and what peels off — so revenue is the informational
source, the flow-through spine (gross profit → operating income → pretax → net
income) wears the profit chain's green, and everything that leaves wears the
cost chain's red. A pastel categorical set here encodes nothing: the reader has
to consult a legend to learn that "the orange one" is tax.

`arrangement="fixed"` with an explicit (x, y) per node is load-bearing rather
than cosmetic. Supplying x alone is not enough: Plotly right-aligns every node
with no outbound link, so Cost of Revenue and SG&A land in the final column and
their ribbons stretch the full width of the canvas — the largest, loudest area
in the figure attached to the least insight.

Ported from sra5's `skills/fetch_fundamental/sankey.py`, with the palette
reconciled to §17.3 (the chain colors are now the status colors at the spec's
alphas, derived through `rgba()` so node and link cannot drift apart) and the
input changed from a pandas frame to the stored `income_statement_yahoo` JSON.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from lib.charts.base import (
    BASE_FONT, CHART_WIDTH, DOWN, INFO, MARGINS, MUTED, NAVY, SANKEY_HEIGHT,
    UP, ChartResult, rgba, write_candidate)

# --- §17.3 palette ----------------------------------------------------------
NODE_REVENUE = INFO                     # the source
NODE_FLOW = rgba(UP, 0.85)              # what survives to the next stage
NODE_COST = rgba(DOWN, 0.85)            # anything that peels off
LINK_FLOW = rgba(UP, 0.18)
LINK_COST = rgba(DOWN, 0.16)
NODE_GRAY = MUTED

# --- P&L columns ------------------------------------------------------------
# The topology is deterministic, so every node's x is known up front.
COL_REVENUE, COL_GROSS, COL_OPERATING, COL_PRETAX, COL_NET = range(5)
N_COLUMNS = 5

# §17.3: fold components under this share of revenue into "Other".
SMALL_NODE_PCT = 0.0025

CHART_NAME = "income_sankey"


def line_items(period: dict) -> dict[str, float]:
    """Normalize one period's raw statement rows into the items the graph needs.

    Providers spell the same line item several ways (`Total Revenue` vs
    `TotalRevenue`), and omit items that are derivable, so this both aliases and
    derives. Everything downstream reads these keys and nothing else.
    """
    def value(key: str) -> float:
        raw = period.get(key)
        if raw is None:
            return 0.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def first(*keys: str) -> float:
        for key in keys:
            found = value(key)
            if found != 0.0:
                return found
        return 0.0

    items = {
        "total_revenue": first("Total Revenue", "TotalRevenue"),
        "cost_of_revenue": first("Cost Of Revenue", "CostOfRevenue"),
        "gross_profit": first("Gross Profit", "GrossProfit"),
        "operating_expense": first("Operating Expense", "OperatingExpense"),
        "selling_ga": first("Selling General And Administration",
                            "SellingGeneralAndAdministration"),
        "research_dev": first("Research And Development", "ResearchAndDevelopment",
                              "Research Development"),
        "other_operating": first("Other Operating Expenses", "OtherOperatingExpenses"),
        "operating_income": first("Operating Income", "OperatingIncome", "EBIT"),
        "interest_expense": abs(first("Interest Expense", "InterestExpense",
                                      "Interest Expense Non Operating",
                                      "InterestExpenseNonOperating")),
        "tax_provision": abs(first("Tax Provision", "TaxProvision",
                                   "Income Tax Expense", "IncomeTaxExpense")),
        "other_income": first("Other Income Expense", "OtherIncomeExpense",
                              "Other Non Operating Income Expenses"),
        "net_income": first("Net Income", "NetIncome",
                            "Net Income Common Stockholders",
                            "NetIncomeCommonStockholders"),
        "pretax_income": first("Pretax Income", "PretaxIncome"),
    }

    if items["gross_profit"] == 0 and items["total_revenue"] > 0 \
            and items["cost_of_revenue"] > 0:
        items["gross_profit"] = items["total_revenue"] - items["cost_of_revenue"]
    if items["cost_of_revenue"] == 0 and items["total_revenue"] > 0 \
            and items["gross_profit"] > 0:
        items["cost_of_revenue"] = items["total_revenue"] - items["gross_profit"]
    if items["operating_expense"] == 0 and (items["selling_ga"] > 0
                                            or items["research_dev"] > 0):
        items["operating_expense"] = (items["selling_ga"] + items["research_dev"]
                                      + items["other_operating"])
    if items["operating_income"] == 0 and items["gross_profit"] > 0:
        items["operating_income"] = items["gross_profit"] - items["operating_expense"]
    return items


def fmt(value: float) -> str:
    """A dollar value at the magnitude a node label can carry."""
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"${magnitude / 1e9:.1f}B"
    if magnitude >= 1e6:
        return f"${magnitude / 1e6:.1f}M"
    if magnitude >= 1e3:
        return f"${magnitude / 1e3:.1f}K"
    return f"${magnitude:.0f}"


class Builder:
    """Accumulates the nodes and links, then computes their fixed positions."""

    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.node_colors: list[str] = []
        self.node_columns: list[int] = []
        self.tail_nodes: set[int] = set()      # folded residuals, sorted to bottom
        self.links_source: list[int] = []
        self.links_target: list[int] = []
        self.links_value: list[float] = []
        self.links_color: list[str] = []

    def add_node(self, name: str, color: str = NODE_GRAY,
                 column: int = COL_OPERATING) -> int:
        self.nodes.append(name)
        self.node_colors.append(color)
        self.node_columns.append(column)
        return len(self.nodes) - 1

    def add_link(self, src: int, tgt: int, value: float,
                 color: str = LINK_COST) -> None:
        if value > 0:
            self.links_source.append(src)
            self.links_target.append(tgt)
            self.links_value.append(value)
            self.links_color.append(color)

    def node_value(self, i: int) -> float:
        """A node's thickness: the larger of what flows in and what flows out."""
        inbound = sum(v for t, v in zip(self.links_target, self.links_value) if t == i)
        outbound = sum(v for s, v in zip(self.links_source, self.links_value) if s == i)
        return max(inbound, outbound)

    def positions(self, gap: float = 0.09) -> tuple[list[float], list[float]]:
        """Explicit (x, y) per node, derived from its P&L column.

        Heights are scaled GLOBALLY, not per column: Plotly derives a node's
        drawn thickness from its value against one diagram-wide scale, so
        normalizing each column to full height instead would put the computed
        centers out of step with the drawn thicknesses — which shows up as
        nodes floating away from their own ribbons.

        Within a column, costs stack above the flow-through node so the
        surviving band reads as one continuous line left to right, and a folded
        residual sinks to the bottom, keeping its faint long-haul ribbon out of
        the main diagonal.

        `gap` has to clear a LABEL, not a node: labels are two lines (~28px) and
        a small node can be a few pixels tall, so a gap sized to the marks alone
        leaves adjacent text touching.
        """
        span = N_COLUMNS - 1
        count = len(self.nodes)
        xs = [0.0] * count
        ys = [0.0] * count

        by_column: dict[int, list[int]] = defaultdict(list)
        for i, column in enumerate(self.node_columns):
            by_column[column].append(i)

        values = {i: self.node_value(i) for i in range(count)}
        global_total = max(
            (sum(values[i] for i in members) for members in by_column.values()),
            default=0.0)
        if global_total <= 0:
            return [0.5] * count, [0.5] * count

        for column, members in by_column.items():
            members.sort(key=lambda i: (i in self.tail_nodes,
                                        self.node_colors[i] != NODE_COST,
                                        i))
            gaps = gap * max(len(members) - 1, 0)
            available = max(1.0 - gaps, 0.05)
            heights = [values[i] / global_total * available for i in members]
            cursor = max((1.0 - (sum(heights) + gaps)) / 2, 0.0)
            for i, height in zip(members, heights):
                # Plotly anchors a node at its center, and treats exact 0 and 1
                # as "unset" — hence the clamp.
                xs[i] = min(max(column / span, 0.001), 0.999)
                ys[i] = min(max(cursor + height / 2, 0.001), 0.999)
                cursor += height + gap
        return xs, ys


def _operating_expenses(b: Builder, n_gross: int, items: dict) -> None:
    """Gross profit → the operating cost breakdown, or one lumped node."""
    detailed = (items["selling_ga"] > 0 or items["research_dev"] > 0
                or items["other_operating"] > 0)
    if detailed:
        for key, label in (("selling_ga", "SG&A"), ("research_dev", "R&D"),
                           ("other_operating", "Other OpEx")):
            if items[key] > 0:
                node = b.add_node(f"{label}<br>{fmt(items[key])}", NODE_COST,
                                  COL_OPERATING)
                b.add_link(n_gross, node, items[key], LINK_COST)
    elif items["operating_expense"] > 0:
        node = b.add_node(f"Operating Expenses<br>{fmt(items['operating_expense'])}",
                          NODE_COST, COL_OPERATING)
        b.add_link(n_gross, node, items["operating_expense"], LINK_COST)


def _profitable_path(b: Builder, n_gross: int, items: dict) -> None:
    operating = items["operating_income"]
    n_op = b.add_node(f"Operating Income<br>{fmt(operating)}", NODE_FLOW,
                      COL_OPERATING)
    b.add_link(n_gross, n_op, operating, LINK_FLOW)

    if items["interest_expense"] > 0:
        node = b.add_node(f"Interest<br>{fmt(items['interest_expense'])}",
                          NODE_COST, COL_PRETAX)
        b.add_link(n_op, node, items["interest_expense"], LINK_COST)
    if items["other_income"] < 0:
        node = b.add_node(f"Other Expense<br>{fmt(items['other_income'])}",
                          NODE_COST, COL_PRETAX)
        b.add_link(n_op, node, abs(items["other_income"]), LINK_COST)

    pretax = items["pretax_income"]
    if pretax == 0 and operating > 0:
        pretax = operating - items["interest_expense"] + items["other_income"]
    if pretax > 0:
        _pretax_positive(b, n_op, pretax, items)
    elif pretax < 0:
        _pretax_negative(b, n_op, pretax, items)


def _pretax_positive(b: Builder, n_op: int, pretax: float, items: dict) -> None:
    n_pretax = b.add_node(f"Pretax Income<br>{fmt(pretax)}", NODE_FLOW, COL_PRETAX)
    b.add_link(n_op, n_pretax, pretax, LINK_FLOW)

    if items["other_income"] > 0:
        # Non-operating money coming IN is value that survives to the next
        # stage, so it wears the flow chain rather than a fourth semantic color
        # (§17.1: a figure needing a color absent from the palette needs
        # rethinking, not a new constant).
        node = b.add_node(f"Other Income<br>{fmt(items['other_income'])}",
                          NODE_FLOW, COL_OPERATING)
        b.add_link(node, n_pretax, items["other_income"], LINK_FLOW)

    if items["tax_provision"] > 0:
        node = b.add_node(f"Taxes<br>{fmt(items['tax_provision'])}", NODE_COST,
                          COL_NET)
        b.add_link(n_pretax, node, items["tax_provision"], LINK_COST)

    net = items["net_income"]
    if net > 0:
        node = b.add_node(f"Net Income<br>{fmt(net)}", NODE_FLOW, COL_NET)
        b.add_link(n_pretax, node, net, LINK_FLOW)
    elif net < 0:
        node = b.add_node(f"Net Loss<br>{fmt(net)}", NODE_COST, COL_NET)
        remaining = pretax - items["tax_provision"]
        b.add_link(n_pretax, node, remaining, LINK_COST)
    else:
        remaining = pretax - items["tax_provision"]
        if remaining > 0:
            node = b.add_node(f"Net Income<br>{fmt(remaining)}", NODE_FLOW, COL_NET)
            b.add_link(n_pretax, node, remaining, LINK_FLOW)


def _pretax_negative(b: Builder, n_op: int, pretax: float, items: dict) -> None:
    n_pretax = b.add_node(f"Pretax Loss<br>{fmt(pretax)}", NODE_COST, COL_PRETAX)
    remaining = items["operating_income"] - items["interest_expense"]
    if items["other_income"] < 0:
        remaining -= abs(items["other_income"])
    b.add_link(n_op, n_pretax, remaining, LINK_COST)

    if items["net_income"] < 0:
        if items["tax_provision"] > 0:
            node = b.add_node(f"Taxes<br>{fmt(items['tax_provision'])}",
                              NODE_COST, COL_NET)
            b.add_link(n_pretax, node, items["tax_provision"], LINK_COST)
        node = b.add_node(f"Net Loss<br>{fmt(items['net_income'])}", NODE_COST,
                          COL_NET)
        b.add_link(n_pretax, node, abs(items["net_income"]), LINK_COST)


def _loss_path(b: Builder, n_gross: int, items: dict) -> None:
    """The operating-loss topology: the spine itself is a cost chain."""
    operating = abs(items["operating_income"])
    net = items["net_income"]
    n_loss = b.add_node(f"Operating Loss<br>{fmt(items['operating_income'])}",
                        NODE_COST, COL_OPERATING)
    b.add_link(n_gross, n_loss, operating, LINK_COST)

    if net < 0:
        net_abs = abs(net)
        n_net = b.add_node(f"Net Loss<br>{fmt(net)}", NODE_COST, COL_NET)
        if operating > net_abs + 1e6:          # non-operating items eased it
            offset = operating - net_abs
            node = b.add_node(f"Interest/Other Income<br>{fmt(offset)}",
                              NODE_FLOW, COL_PRETAX)
            b.add_link(n_loss, node, offset, LINK_FLOW)
            b.add_link(n_loss, n_net, net_abs, LINK_COST)
        elif net_abs > operating + 1e6:        # non-operating items worsened it
            b.add_link(n_loss, n_net, operating, LINK_COST)
            node = b.add_node(
                f"Interest/Other Charges<br>{fmt(net_abs - operating)}",
                NODE_COST, COL_PRETAX)
            b.add_link(node, n_net, net_abs - operating, LINK_COST)
        else:
            b.add_link(n_loss, n_net, net_abs, LINK_COST)
    elif net > 0:
        n_net = b.add_node(f"Net Income<br>{fmt(net)}", NODE_FLOW, COL_NET)
        node = b.add_node(f"Interest/Other Income<br>{fmt(operating + net)}",
                          NODE_FLOW, COL_PRETAX)
        b.add_link(node, n_net, net, LINK_FLOW)
        b.add_link(n_loss, node, operating, LINK_FLOW)


def fold_small_nodes(b: Builder, revenue: float,
                     threshold_pct: float = SMALL_NODE_PCT) -> int:
    """Merge negligible terminal costs into one "Other" node (§17.3).

    Only TERMINAL SINKS are eligible, so the revenue → gross → operating →
    pretax → net spine is never rewired — folding a stage would break the one
    thing the diagram exists to show.

    Folding a single node buys nothing: "Other $4M" is exactly as wide as
    "Taxes $4M", and it costs the reader the name of the line item. The
    collision only clears when two or more merge.
    """
    if revenue <= 0:
        return 0
    threshold = revenue * threshold_pct

    inbound: dict[int, float] = defaultdict(float)
    has_outbound = set()
    for src, tgt, value in zip(b.links_source, b.links_target, b.links_value):
        inbound[tgt] += value
        has_outbound.add(src)

    fold = {i for i in range(len(b.nodes))
            if i not in has_outbound
            and 0 < inbound.get(i, 0.0) < threshold
            and b.node_colors[i] == NODE_COST}
    if len(fold) < 2:
        return 0

    total = sum(inbound[i] for i in fold)
    column = max(b.node_columns[i] for i in fold)

    keep = [i for i in range(len(b.nodes)) if i not in fold]
    remap = {old: new for new, old in enumerate(keep)}
    b.nodes = [b.nodes[i] for i in keep]
    b.node_colors = [b.node_colors[i] for i in keep]
    b.node_columns = [b.node_columns[i] for i in keep]
    n_other = b.add_node(f"Other<br>{fmt(total)}", NODE_COST, column)
    b.tail_nodes = {n_other}

    src_out: list[int] = []
    tgt_out: list[int] = []
    val_out: list[float] = []
    clr_out: list[str] = []
    merged_from: dict[int, int] = {}
    for src, tgt, value, color in zip(b.links_source, b.links_target,
                                      b.links_value, b.links_color):
        if tgt in fold:
            new_src = remap.get(src)
            if new_src is None:
                continue                      # source folded too; already counted
            if new_src in merged_from:
                val_out[merged_from[new_src]] += value
                continue
            merged_from[new_src] = len(src_out)
            src_out.append(new_src)
            tgt_out.append(n_other)
            val_out.append(value)
            clr_out.append(LINK_COST)
        elif src not in fold:
            src_out.append(remap[src])
            tgt_out.append(remap[tgt])
            val_out.append(value)
            clr_out.append(color)
    b.links_source, b.links_target = src_out, tgt_out
    b.links_value, b.links_color = val_out, clr_out
    return len(fold)


def build_graph(items: dict) -> Builder:
    b = Builder()
    n_revenue = b.add_node(f"Revenue<br>{fmt(items['total_revenue'])}",
                           NODE_REVENUE, COL_REVENUE)
    n_cogs = b.add_node(f"Cost of Revenue<br>{fmt(items['cost_of_revenue'])}",
                        NODE_COST, COL_GROSS)
    n_gross = b.add_node(f"Gross Profit<br>{fmt(items['gross_profit'])}",
                         NODE_FLOW, COL_GROSS)
    b.add_link(n_revenue, n_cogs, items["cost_of_revenue"], LINK_COST)
    b.add_link(n_revenue, n_gross, items["gross_profit"], LINK_FLOW)

    _operating_expenses(b, n_gross, items)
    if items["operating_income"] > 0:
        _profitable_path(b, n_gross, items)
    elif items["operating_income"] < 0:
        _loss_path(b, n_gross, items)

    fold_small_nodes(b, items["total_revenue"])
    return b


def build_figure(items: dict):
    """The Sankey figure. Pure function of the normalized line items."""
    import plotly.graph_objects as go

    b = build_graph(items)
    node_x, node_y = b.positions()
    fig = go.Figure(data=[go.Sankey(
        arrangement="fixed",
        node=dict(pad=28, thickness=25, line=dict(color=NAVY, width=0.5),
                  label=b.nodes, color=b.node_colors, x=node_x, y=node_y),
        link=dict(source=b.links_source, target=b.links_target,
                  value=b.links_value, color=b.links_color),
    )])
    # Not `apply_base_layout`: a Sankey has no axes, and the axis half of §17.4
    # would only leave an unrendered axis object in the layout. The font, the
    # geometry, the margins and the no-title rule all still apply.
    fig.update_layout(title=None, showlegend=False, font=BASE_FONT,
                      paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      width=CHART_WIDTH, height=SANKEY_HEIGHT, margin=MARGINS)
    return fig


def read_income(ticker_dir: Path) -> tuple[str, dict] | None:
    """`(period, rows)` for the latest period carrying revenue, or `None`."""
    path = ticker_dir / "structured" / "income_statement_yahoo.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None

    for period in sorted(data, reverse=True):
        rows = data[period]
        if isinstance(rows, dict) and line_items(rows)["total_revenue"] > 0:
            return period, rows
    return None


def render_income_sankey(ticker_dir: Path, *,
                         write_png: bool = True) -> ChartResult | None:
    """Render `income_sankey`, or `None` when the statement is unusable."""
    found = read_income(ticker_dir)
    if found is None:
        return None
    period, rows = found
    items = line_items(rows)

    fig = build_figure(items)
    margin = (items["net_income"] / items["total_revenue"] * 100
              if items["total_revenue"] else 0.0)
    return write_candidate(
        ticker_dir, fig,
        name=CHART_NAME,
        title=f"{ticker_dir.name.upper()} income statement flow, FY ending {period}",
        data_sources=["income_statement_yahoo"],
        auto_caption=(
            f"Revenue of {fmt(items['total_revenue'])} through to net income of "
            f"{fmt(items['net_income'])} ({margin:.1f}% net margin), fiscal year "
            f"ending {period}. Components under {SMALL_NODE_PCT:.2%} of revenue "
            f"are folded into Other. Source: Yahoo Finance."),
        salience={
            "recency_days": 0,
            "coverage": 1.0,
            "variance_note": f"single period, FY ending {period}",
        },
        height=SANKEY_HEIGHT,
        write_png=write_png)
