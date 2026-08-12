"""The income-statement Sankey (spec §17.3).

Color here is semantic, not categorical: an income statement tells one story —
what keeps flowing right and what peels off — so the profit chain is green, the
cost chain is red, and revenue is informational blue. This and the candlesticks
are the only two places §17.1 permits red and green to carry meaning, which is
why the tests below pin every node and link color rather than sampling one.

The other half of these tests is geometry. `arrangement="fixed"` with an
explicit (x, y) per node is what pins a cost to the stage where it is incurred;
without it Plotly right-aligns every node that has no outbound link, so cost of
revenue lands in the final column and its ribbon stretches the full width of the
canvas — the largest, loudest area in the figure attached to the least insight.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.charts import base, sankey

B = 1_000_000_000


def income_data(**overrides) -> dict:
    """One profitable annual period, in `income_statement_yahoo`'s stored shape."""
    period = {
        "Total Revenue": 8.0 * B,
        "Cost Of Revenue": 2.4 * B,
        "Gross Profit": 5.6 * B,
        "Research And Development": 1.6 * B,
        "Selling General And Administration": 2.6 * B,
        "Operating Income": 1.4 * B,
        "Interest Expense": 0.05 * B,
        "Pretax Income": 1.4 * B,
        "Tax Provision": 0.3 * B,
        "Net Income": 1.1 * B,
    }
    period.update(overrides)
    return {"2026-07-31": period, "2025-07-31": dict(period)}


def write_income(ticker_dir: Path, data: dict | None = None) -> Path:
    path = ticker_dir / "structured" / "income_statement_yahoo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_meta": {"id": "income_statement_yahoo", "ticker": "PANW",
                  "producer": "fetch", "title": "PANW income statement (annual)",
                  "source": "Yahoo Finance",
                  "url": "https://finance.yahoo.com/quote/PANW/financials",
                  "provider_tool": "yfinance.Ticker.income_stmt",
                  "fetch_cmd": "uv run python sra.py prefetch PANW --kinds financials",
                  "fetched_at": "2026-08-11T12:00:00+00:00",
                  "as_of": "2026-07-31", "period": "annual", "currency": "USD"},
        "data": data if data is not None else income_data()}), encoding="utf-8")
    return path


@pytest.fixture
def figure():
    return sankey.build_figure(sankey.line_items(income_data()["2026-07-31"]))


def trace(fig):
    return fig.data[0]


def labels_of(fig) -> list[str]:
    return [label.split("<br>")[0] for label in trace(fig).node.label]


def node_index(fig, label: str) -> int:
    return labels_of(fig).index(label)


# --- §17.3 semantic color --------------------------------------------------

def test_revenue_is_informational_not_semantic(figure):
    assert trace(figure).node.color[node_index(figure, "Revenue")] == base.INFO


def test_the_profit_chain_is_green_at_the_spec_alphas(figure):
    node_colors = trace(figure).node.color
    for stage in ("Gross Profit", "Operating Income", "Pretax Income",
                  "Net Income"):
        assert node_colors[node_index(figure, stage)] == "rgba(26,127,55,0.85)"
    assert "rgba(26,127,55,0.18)" in set(trace(figure).link.color)


def test_the_cost_chain_is_red_at_the_spec_alphas(figure):
    node_colors = trace(figure).node.color
    for cost in ("Cost of Revenue", "R&D", "SG&A", "Taxes"):
        assert node_colors[node_index(figure, cost)] == "rgba(179,38,30,0.85)"
    assert "rgba(179,38,30,0.16)" in set(trace(figure).link.color)


def test_link_alphas_are_lower_than_node_alphas(figure):
    """Cost of revenue is the largest area in the figure; at a node's opacity
    the cost slab would be the loudest object on the page while carrying the
    least insight."""
    assert sankey.LINK_FLOW == base.rgba(base.UP, 0.18)
    assert sankey.LINK_COST == base.rgba(base.DOWN, 0.16)


def test_nodes_carry_the_navy_border(figure):
    line = trace(figure).node.line
    assert line.color == base.NAVY
    assert line.width == 0.5


# --- §17.3 geometry --------------------------------------------------------

def test_arrangement_is_fixed_with_explicit_positions(figure):
    node = trace(figure).node
    assert trace(figure).arrangement == "fixed"
    assert node.pad == 28
    assert node.x is not None and node.y is not None
    assert len(node.x) == len(node.label) == len(node.y)


def test_x_positions_land_on_the_five_p_and_l_columns(figure):
    """The income-statement topology is deterministic, so every node's column is
    known up front — letting Plotly's solver place them crowds the tail and
    overprints the small labels."""
    columns = {round(x, 3) for x in trace(figure).node.x}
    allowed = {0.001, 0.25, 0.5, 0.75, 0.999}
    assert columns <= allowed, columns


def test_each_stage_sits_in_its_own_column(figure):
    xs = trace(figure).node.x
    stages = ["Revenue", "Gross Profit", "Operating Income", "Pretax Income",
              "Net Income"]
    positions = [xs[node_index(figure, s)] for s in stages]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(stages)


def test_costs_are_pinned_to_the_stage_where_they_are_incurred(figure):
    xs = trace(figure).node.x
    assert xs[node_index(figure, "Cost of Revenue")] == \
        xs[node_index(figure, "Gross Profit")]
    assert xs[node_index(figure, "R&D")] == \
        xs[node_index(figure, "Operating Income")]


def test_figure_geometry(figure):
    assert figure.layout.width == base.CHART_WIDTH
    assert figure.layout.height == base.SANKEY_HEIGHT
    assert figure.layout.title.text is None
    assert figure.layout.margin.t == 8


# --- folding ---------------------------------------------------------------

def test_negligible_costs_fold_into_other(figure):
    """§17.3: components under 0.25% of revenue. A $4M tax provision against
    $8B of revenue cannot be drawn at a legible width, and it costs two label
    collisions where its leader line trails off the plot."""
    tiny = income_data(**{"Tax Provision": 0.004 * B,
                          "Interest Expense": 0.003 * B})
    fig = sankey.build_figure(sankey.line_items(tiny["2026-07-31"]))
    labels = labels_of(fig)
    assert "Other" in labels
    assert "Taxes" not in labels and "Interest" not in labels


def test_a_lone_small_node_is_not_folded(figure):
    """"Other $4M" is exactly as wide as "Taxes $4M" — folding one node buys
    nothing, and it costs the reader the name of the line item."""
    one_tiny = income_data(**{"Tax Provision": 0.004 * B})
    fig = sankey.build_figure(sankey.line_items(one_tiny["2026-07-31"]))
    assert "Other" not in labels_of(fig)
    assert "Taxes" in labels_of(fig)


def test_folding_never_rewires_the_flow_through_chain():
    """Only terminal sinks are eligible: a folded Gross Profit would break the
    revenue → net income spine the whole diagram exists to show."""
    thin = income_data(**{"Operating Income": 0.001 * B, "Pretax Income": 0.001 * B,
                          "Net Income": 0.0005 * B, "Tax Provision": 0.0005 * B})
    fig = sankey.build_figure(sankey.line_items(thin["2026-07-31"]))
    assert "Gross Profit" in labels_of(fig)
    assert "Operating Income" in labels_of(fig)


# --- loss paths ------------------------------------------------------------

def test_an_operating_loss_renders_as_a_cost_chain():
    loss = income_data(**{"Operating Income": -0.5 * B, "Pretax Income": -0.55 * B,
                          "Net Income": -0.45 * B, "Tax Provision": 0.0})
    fig = sankey.build_figure(sankey.line_items(loss["2026-07-31"]))
    labels = labels_of(fig)
    assert "Operating Loss" in labels
    assert "Net Loss" in labels
    colors = trace(fig).node.color
    assert colors[labels.index("Operating Loss")] == "rgba(179,38,30,0.85)"


def test_derived_values_fill_in_for_a_missing_line_item():
    """Providers omit `Gross Profit` for some issuers; revenue minus cost of
    revenue is the same number and keeps the chain intact."""
    items = sankey.line_items({"Total Revenue": 100.0, "Cost Of Revenue": 40.0,
                               "Operating Income": 25.0, "Net Income": 20.0})
    assert items["gross_profit"] == 60.0


# --- the renderer ----------------------------------------------------------

def test_renderer_writes_a_candidate_with_lineage_and_period(tmp_ticker_dir: Path):
    write_income(tmp_ticker_dir)
    result = sankey.render_income_sankey(tmp_ticker_dir, write_png=False)

    assert isinstance(result, base.ChartResult)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["name"] == "income_sankey"
    assert manifest["data_sources"] == ["income_statement_yahoo"]
    assert "2026-07-31" in manifest["auto_caption"]
    assert "Yahoo Finance" in manifest["auto_caption"]


def test_renderer_uses_the_latest_period(tmp_ticker_dir: Path):
    data = income_data()
    data["2025-07-31"]["Total Revenue"] = 1.0 * B
    write_income(tmp_ticker_dir, data)
    result = sankey.render_income_sankey(tmp_ticker_dir, write_png=False)
    caption = json.loads(result.manifest_path.read_text())["auto_caption"]
    assert "2026-07-31" in caption and "$8.0B" in caption


def test_renderer_returns_none_without_the_statement(tmp_ticker_dir: Path):
    assert sankey.render_income_sankey(tmp_ticker_dir, write_png=False) is None


def test_renderer_returns_none_without_revenue(tmp_ticker_dir: Path):
    """No revenue means no source node, and every ribbon is measured as a share
    of it — there is nothing to draw."""
    write_income(tmp_ticker_dir, {"2026-07-31": {"Net Income": 5.0}})
    assert sankey.render_income_sankey(tmp_ticker_dir, write_png=False) is None


def test_renderer_is_registered_for_the_verdict_independent_pass():
    from lib.charts import registry

    assert registry.RENDERERS["income_sankey"].requires_verdict is False
