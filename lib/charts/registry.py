#!/usr/bin/env python3
"""The renderer registry (spec §16.1, §16.3, §20).

One table maps a chart name to the function that renders it. `sra.py charts`
walks the table rather than importing renderers by hand, so adding an exhibit is
one entry here and nothing else.

Two contracts every renderer keeps:

- `fn(ticker_dir) -> ChartResult | None`. `None` means the inputs are not on
  disk, which is normal degraded behavior (§16.1) and never an error.
- `requires_verdict` splits the two passes (§16.4). Conclusion-dependent
  exhibits — the football field, the DCF — cannot render until the polish chain
  has written `verdict.json`, so they sit out `charts T` and run in
  `charts T --verdict`.

This module deliberately imports no plotly: it is the table, not a renderer, and
keeping it light means the CLI can list what exists without loading a graphing
stack.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from lib.charts import calendar, fundamentals, macro, peers, price, sankey
from lib.charts.base import ChartResult


@dataclass(frozen=True)
class Renderer:
    name: str
    fn: Callable[[Path], ChartResult | None]
    requires_verdict: bool = False


def select(renderers: dict[str, Renderer], *, verdict: bool) -> Iterator[str]:
    """Names of the renderers belonging to one pass, in registry order."""
    for name, renderer in renderers.items():
        if renderer.requires_verdict is verdict:
            yield name


# Populated as renderer modules land (Tasks 10.2-10.4). Order is display order:
# `charts` reports in this sequence, and the chartbook subagent reads the
# candidate manifests in the order they were written.
RENDERERS: dict[str, Renderer] = {
    price.CHART_NAME: Renderer(price.CHART_NAME, price.render_price_weekly),
    sankey.CHART_NAME: Renderer(sankey.CHART_NAME, sankey.render_income_sankey),
    "revenue_growth": Renderer("revenue_growth", fundamentals.render_revenue_growth),
    "margin_trends": Renderer("margin_trends", fundamentals.render_margin_trends),
    "fcf_conversion": Renderer("fcf_conversion", fundamentals.render_fcf_conversion),
    "forward_multiple_vs_history": Renderer("forward_multiple_vs_history",
                                            fundamentals.render_forward_multiple),
    "peer_scatter": Renderer("peer_scatter", peers.render_peer_scatter),
    "peer_multiples": Renderer("peer_multiples", peers.render_peer_multiples),
    "catalyst_calendar": Renderer("catalyst_calendar",
                                  calendar.render_catalyst_calendar),
    "macro_rates": Renderer("macro_rates", macro.render_macro_rates),
    "macro_market_valuation": Renderer("macro_market_valuation",
                                       macro.render_macro_market_valuation),
}
