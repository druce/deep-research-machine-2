#!/usr/bin/env python3
"""Shared chart style and the candidate-writing path (spec §16.1, §17).

Every figure in the report is rendered through this module, so the exhibits
cannot drift from each other or from `templates/report.css`, which shares this
palette. §17.1 opens with the rule that governs the whole file: **do not invent
new colors** — a figure that needs a hex absent from §17.1 needs rethinking, not
a new constant here.

The categorical set was computed with a contrast validator, not chosen by eye;
the WARN on `#1baf7a` (2.82:1 on white, under the 3:1 swatch-relief threshold)
is why `NEEDS_DIRECT_LABEL` exists and why a series wearing it may not be
identified by a legend swatch alone.

Two traps worth knowing before writing a renderer:

- **Charts never fetch** (§16.3). A renderer is a pure function of persisted
  artifacts, and returns `None` when its inputs are not there.
- **kaleido cannot serialize `pandas.Timestamp`.** Trace `x` values must be ISO
  date strings; a `DatetimeIndex` passed straight through fails inside orjson at
  PNG-write time, long after the figure looked fine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# --- §17.1 chrome and ink ---------------------------------------------------
INK = "#23282f"        # body ink, labels, price line
MUTED = "#5b636e"      # annotations, volume
NAVY = "#0f2942"       # panel-style axis titles, node borders
RULE = "#dde1e6"       # axis and reference lines
GRID = "#eef0f3"       # horizontal gridlines

# --- §17.1 price-chart series slots ----------------------------------------
# The slot IS the meaning. A reader who learns orange = relative strength on one
# exhibit must not meet orange as a moving average on the next, so these are
# fixed assignments and are NEVER cycled as a palette.
S1_MA13 = "#2a78d6"
S2_MA52 = "#4a3aa7"
S3_VOLUME = "#5b636e"
S4_RS = "#eb6834"
SERIES_SLOTS = (S1_MA13, S2_MA52, S3_VOLUME, S4_RS)

# --- §17.1 categorical set --------------------------------------------------
CATEGORICAL = ("#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7")
CATEGORICAL_MAX = len(CATEGORICAL)
OTHER_LABEL = "Other"

# Below the 3:1 legend-swatch threshold on white: identify by a direct label.
NEEDS_DIRECT_LABEL = ("#1baf7a",)

# --- §17.1 status colors ----------------------------------------------------
UP = "#1a7f37"
DOWN = "#b3261e"

# Candles and the Sankey's semantic chains are the ONLY places red and green
# carry meaning (§17.1). Nowhere else may a series be colored by sign or by
# sentiment — and in candles, color is not sufficient on its own: shape
# encoding (hollow vs filled) is mandatory (§17.2).
SEMANTIC_COLOR_USES = ("candles", "sankey")

# §17.3's revenue node: informational, not semantic.
INFO = "#1a5fb4"

# --- §17.4 typography and geometry ------------------------------------------
FONT_FAMILY = "Helvetica Neue, Helvetica, Arial, sans-serif"
FONT_SIZE = 11

BASE_FONT = dict(family=FONT_FAMILY, size=FONT_SIZE, color=INK)
AXIS_TITLE_FONT = dict(family=FONT_FAMILY, size=FONT_SIZE, color=NAVY)
SMALL_AXIS_TITLE_FONT = dict(family=FONT_FAMILY, size=10, color=MUTED)

CHART_WIDTH = 980
CHART_SCALE = 2
PRICE_HEIGHT = 520
SANKEY_HEIGHT = 420
MARGINS = dict(l=52, r=64, t=8, b=28)

CANDIDATES_SUBDIR = "charts/candidates"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def rgba(hex_color: str, alpha: float) -> str:
    """`#rrggbb` at `alpha`, as an `rgba(...)` string.

    Plotly's Sankey links need alpha, and §17.3's link colors are exactly the
    status colors at low opacity. Deriving them from the same constants keeps
    node and link provably in step instead of relying on two hand-typed hexes
    agreeing.
    """
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #rrggbb, got {hex_color!r}")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def fold_categories(items: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep the largest `CATEGORICAL_MAX - 1` categories, sum the rest into
    "Other" (§17.1).

    The tail is folded rather than dropped so the parts still sum to the whole:
    a mix chart whose slices silently omit the tail misstates every share on it.
    """
    if len(items) <= CATEGORICAL_MAX:
        return items
    ranked = sorted(items, key=lambda kv: kv[1], reverse=True)
    head = ranked[:CATEGORICAL_MAX - 1]
    tail = sum(value for _, value in ranked[CATEGORICAL_MAX - 1:])
    return [*head, (OTHER_LABEL, tail)]


def apply_base_layout(fig, height: int | None = None) -> None:
    """Apply §17.4 to `fig`: the report's font, recessive axes, no chrome.

    Plotly's defaults are wrong for print in four specific ways, and each line
    below undoes one: `plotly_white` grids in both directions (§17.4 wants
    horizontal only), candlesticks turn the rangeslider on (it prints as a gray
    smear at PDF scale), figures draw their own title (the template owns the
    exhibit heading and caption), and a legend would identify series that §17.2
    requires to be directly labeled instead.
    """
    fig.update_layout(
        title=None,
        showlegend=False,
        font=BASE_FONT,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        width=CHART_WIDTH,
        margin=MARGINS,
        **({"height": height} if height else {}),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID, gridwidth=1,
        zeroline=False, showline=False,
        ticks="outside", ticklen=4, tickcolor=RULE,
        title_font=AXIS_TITLE_FONT,
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False,
        showline=True, linecolor=RULE, linewidth=1,
        ticks="outside", ticklen=4, tickcolor=RULE,
        title_font=AXIS_TITLE_FONT,
        rangeslider_visible=False,
    )


@dataclass(frozen=True)
class ChartResult:
    """What a renderer returns when it produced an exhibit (§16.1)."""

    name: str
    png_path: Path
    manifest_path: Path


def load_verdict(ticker_dir: Path) -> dict | None:
    """`verdict.json` from the latest report run, or `None` (§15.3, §16.3).

    Prefers `reports/latest/` — the symlink the snapshot step maintains — and
    falls back to the newest run directory that has one, so a `charts --verdict`
    pass works before any snapshot has run.
    """
    reports = ticker_dir / "reports"
    candidates: list[Path] = []
    latest = reports / "latest" / "verdict.json"
    if latest.exists():
        candidates.append(latest)
    else:
        candidates = sorted((p / "verdict.json" for p in reports.glob("*")
                             if p.is_dir()), reverse=True)
    for path in candidates:
        if not path.exists():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def write_candidate(
    ticker_dir: Path,
    fig,
    *,
    name: str,
    title: str,
    data_sources: list[str],
    auto_caption: str,
    salience: dict,
    height: int,
    derived_from_urls: list[str] | None = None,
    write_png: bool = True,
) -> ChartResult:
    """Write `charts/candidates/<name>.{png,json}` and return the result (§16.1).

    `data_sources` must be non-empty. A candidate whose manifest names no input
    cannot be traced back to evidence, and `/sra-chartbook` builds its captions'
    provider and as-of claims out of exactly those ids — an unsourced chart
    would get a caption asserting a provenance nobody can check.

    `write_png=False` renders the manifest only. Tests use it: kaleido drives a
    headless browser, which is far too slow to run per assertion, and what the
    unit tests are checking is the figure object and the manifest rather than
    the pixels.
    """
    if not _SAFE_NAME.match(name):
        raise ValueError(
            f"chart name {name!r} must match {_SAFE_NAME.pattern} — it is "
            f"interpolated into a path (§8.4)")
    if not data_sources:
        raise ValueError(
            f"chart {name!r}: data_sources must be non-empty — a candidate with "
            f"no declared lineage cannot be cited or captioned (§16.1)")

    out_dir = ticker_dir / CANDIDATES_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{name}.png"
    manifest_path = out_dir / f"{name}.json"

    if write_png:
        fig.write_image(png_path, width=CHART_WIDTH, height=height,
                        scale=CHART_SCALE)

    manifest = {
        "name": name,
        "title": title,
        "data_sources": list(data_sources),
        "derived_from_urls": list(derived_from_urls or []),
        "auto_caption": auto_caption,
        "salience": dict(salience),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    return ChartResult(name=name, png_path=png_path, manifest_path=manifest_path)
