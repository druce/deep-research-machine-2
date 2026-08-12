#!/usr/bin/env python3
"""Markdown post-processing and template value formatting (spec §15.3).

Ported from SRA5's `skills/render_final.py`, which §15.3 names explicitly:
"Reuse the sra5 rendering pipeline and CSS fixes, including `align_numeric_columns`,
pagetitle handling, empty-alt image handling."

Everything here is a pure string/dict function. The rule that shapes the module:
**pandoc reads structure, not intent.** Column alignment comes from the
separator row and is emitted as an inline style that beats any stylesheet rule;
a non-empty image alt becomes a `<figcaption>` that duplicates the caption the
template already wrote. Section writers produce plain markdown, so the fixes
have to happen between the template and pandoc.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# --- numeric column alignment ---------------------------------------------

_SEPARATOR_RE = re.compile(r"^\s*\|?(\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")
_SCALE_SUFFIX_RE = re.compile(r"(?i)\s*(bn|bps|bp|pp|tn|[bmkt])$")

# Share of decidable body cells that must parse as numbers before a column is
# right-aligned. Below 1.0 so one "55-57% (guidance)" cell in an otherwise
# numeric column does not veto the whole column.
_NUMERIC_COLUMN_THRESHOLD = 0.6


def _cell_is_numeric(cell: str) -> bool | None:
    """True/False for a decidable cell, None for empty or 'N/A' (no signal)."""
    text = re.sub(r"<[^>]+>", "", cell)          # inline HTML (chips)
    text = re.sub(r"[*_`]", "", text).strip()    # markdown emphasis
    if not text or text.upper() in {"N/A", "NA", "-", "—", "–", "?"}:
        return None

    text = text.replace("−", "-")                # unicode minus
    text = re.sub(r"[\$€£¥]", "", text)
    text = text.replace(",", "").replace("%", "")
    text = re.sub(r"(?i)[x×]$", "", text).strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]                  # accounting negatives
    text = _SCALE_SUFFIX_RE.sub("", text).strip()
    text = text.lstrip("+")

    try:
        float(text)
        return True
    except ValueError:
        return False


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def align_numeric_columns(markdown: str) -> str:
    """Right-align numeric columns in every markdown table lacking explicit alignment.

    Separator rows that already carry ':' are left untouched, so alignment set
    deliberately in `final_report.md.j2` always wins. Column 0 stays
    left-aligned: it is the row label even when it looks like a number
    ("FY2022").
    """
    lines = markdown.split("\n")
    out = list(lines)
    in_fence = False
    i = 0

    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or i + 1 >= len(lines):
            i += 1
            continue

        header, separator = lines[i], lines[i + 1]
        if "|" not in header or not _SEPARATOR_RE.match(separator) or ":" in separator:
            i += 1
            continue

        n_cols = len(_split_row(separator))
        body: list[list[str]] = []
        j = i + 2
        while j < len(lines) and "|" in lines[j] and lines[j].strip():
            body.append(_split_row(lines[j]))
            j += 1

        if body:
            numeric = [False] * n_cols
            for col in range(1, n_cols):          # column 0 is the row label
                votes = [
                    v for v in (_cell_is_numeric(r[col]) for r in body if col < len(r))
                    if v is not None
                ]
                if votes and sum(votes) / len(votes) >= _NUMERIC_COLUMN_THRESHOLD:
                    numeric[col] = True
            if any(numeric):
                out[i + 1] = "|" + "|".join(
                    "---:" if numeric[c] else ":---" for c in range(n_cols)
                ) + "|"
        i = j if body else i + 1

    return "\n".join(out)


# --- scenario matrices ----------------------------------------------------
#
# The bear/base/bull table is written by the Valuation section writer, not by
# the template, so it cannot carry a fenced-div class at the source. Detect it
# by its header row and wrap it in `::: {.scenario .base-N}`, where N is the
# 1-indexed column holding the base case — report.css marks that column the way
# the peer table marks the subject row.

_BASE_HEADER_RE = re.compile(r"(?i)^\**\s*base\b")
_BEAR_HEADER_RE = re.compile(r"(?i)^\**\s*bear\b")
_BULL_HEADER_RE = re.compile(r"(?i)^\**\s*bull\b")


def mark_scenario_tables(markdown: str) -> str:
    """Wrap bear/base/bull tables in a `.scenario .base-N` fenced div.

    A table qualifies only when its header row carries all three of bear, base
    and bull, so an ordinary three-column table is never captured.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0

    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(lines[i])
            i += 1
            continue

        if in_fence or i + 1 >= len(lines) or "|" not in lines[i]:
            out.append(lines[i])
            i += 1
            continue

        header_cells = _split_row(lines[i])
        if not _SEPARATOR_RE.match(lines[i + 1]):
            out.append(lines[i])
            i += 1
            continue

        base_col = next(
            (n for n, c in enumerate(header_cells, start=1) if _BASE_HEADER_RE.match(c)),
            None,
        )
        has_wings = (
            any(_BEAR_HEADER_RE.match(c) for c in header_cells)
            and any(_BULL_HEADER_RE.match(c) for c in header_cells)
        )
        if base_col is None or not has_wings:
            out.append(lines[i])
            i += 1
            continue

        j = i + 2
        while j < len(lines) and "|" in lines[j] and lines[j].strip():
            j += 1

        out.append(f"::: {{.scenario .base-{base_col}}}")
        out.extend(lines[i:j])
        out.append(":::")
        i = j

    return "\n".join(out)


# --- signed figures in table cells ----------------------------------------
#
# A cell that is nothing but a signed number ("+7%", "-48%", "(1.2)") gets a
# `.num pos|neg` span so direction reads as colour plus the sign character. Only
# whole-cell matches are wrapped: a signed number inside a sentence keeps body
# ink, and a cell that already contains inline HTML (a chip) is left alone.

_SIGNED_CELL_RE = re.compile(
    r"""^(?P<open>\**)
         (?P<sign>[+\-−])
         (?P<body>[\d.,]+\s*(?:%|x|×|bps?|pp|[BMKT]n?)?)
         (?P<close>\**)$""",
    re.VERBOSE,
)


def colour_signed_cells(markdown: str) -> str:
    """Wrap whole-cell signed numbers in `<span class="num pos|neg">`."""

    def wrap_cell(cell: str) -> str:
        text = cell.strip()
        if not text or "<" in text:
            return cell
        m = _SIGNED_CELL_RE.match(text)
        if not m:
            return cell
        negative = m.group("sign") in "-−"
        klass = "neg" if negative else "pos"
        inner = f'<span class="num {klass}">{m.group("sign")}{m.group("body")}</span>'
        return f' {m.group("open")}{inner}{m.group("close")} '

    lines = markdown.split("\n")
    out = list(lines)
    in_fence = False

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        # Body rows only: a header row has no figures, and rewriting a separator
        # row would destroy the column alignment set above.
        if in_fence or "|" not in line or _SEPARATOR_RE.match(line):
            continue
        if not line.strip().startswith("|"):
            continue

        prefix = line[: len(line) - len(line.lstrip())]
        trailing_pipe = line.rstrip().endswith("|")
        cells = _split_row(line)
        wrapped = [wrap_cell(c) for c in cells]
        if wrapped == cells:
            continue
        out[i] = prefix + "|" + "|".join(wrapped) + ("|" if trailing_pipe else "")

    return "\n".join(out)


# --- empty-alt image handling ---------------------------------------------

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\((?P<target>[^)\n]*)\)")


def blank_image_alts(markdown: str) -> str:
    """Rewrite `![alt](path)` to `![](path)` (§15.3's empty-alt handling).

    Pandoc promotes a non-empty alt to a `<figcaption>`, which duplicates the
    caption the template writes under every exhibit. The template already uses
    empty alts; this catches the ones a section writer embedded by hand.
    """
    return _IMAGE_RE.sub(lambda m: f"![]({m.group('target')})", markdown)


def postprocess(markdown: str) -> str:
    """The full markdown fix-up chain, in the one order that works.

    Alignment reads raw cells, so it runs before the signed-cell pass wraps any
    of them in HTML; scenario detection reads header text, which neither pass
    touches.
    """
    markdown = align_numeric_columns(markdown)
    markdown = mark_scenario_tables(markdown)
    markdown = colour_signed_cells(markdown)
    return blank_image_alts(markdown)


# --- template value formatting --------------------------------------------

def format_price(value: Any) -> str:
    """A price to 2dp, or 'N/A'. Providers return full float precision
    (114.68000030517578) and the template prints what it is given."""
    if value is None or isinstance(value, bool):
        return "N/A"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def format_number(value: Any) -> str:
    """Thousands-separated integer, for volume-shaped figures."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "N/A"


def format_market_cap(value: Any) -> str:
    """A market cap as `2.31T` / `48.7B` / `912.4M`, or 'N/A' (sra5 precision:
    two decimals at trillions, one below, since a tenth of a trillion is still
    a hundred billion dollars of precision nobody needs)."""
    if value is None or isinstance(value, bool):
        return "N/A"
    try:
        magnitude = float(value)
    except (TypeError, ValueError):
        return "N/A"
    sign = "-" if magnitude < 0 else ""
    magnitude = abs(magnitude)
    for cut, suffix, digits in ((1e12, "T", 2), (1e9, "B", 1), (1e6, "M", 1)):
        if magnitude >= cut:
            return f"{sign}{magnitude / cut:.{digits}f}{suffix}"
    return f"{sign}{magnitude:,.0f}"


# A single trailing corporate suffix. Stripped once, not repeatedly: a second
# pass turns "ASML Holding N.V." into bare "ASML".
_CORP_SUFFIX_RE = re.compile(
    r"(?i)[,\s]+(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|"
    r"holdings?|group|n\.v\.|nv|s\.a\.|sa|a\.g\.|ag|se|llc|l\.p\.|lp)\.?\s*$"
)


def shorten_company_name(name: Any, max_len: int = 26) -> str:
    """Drop one trailing corporate suffix so peer names stay on one table line.

    "Lam Research Corporation" -> "Lam Research". Names beyond `max_len` are
    elided; an unconstrained name wraps to three lines and triples row height,
    which destroys the horizontal scan the peer table exists for.
    """
    if not isinstance(name, str) or not name.strip():
        return "N/A"
    out = _CORP_SUFFIX_RE.sub("", name.strip()).strip(" ,")
    if not out:
        out = name.strip()
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip() + "…"
    return out


def format_report_date(timestamp: Any) -> str:
    """An ISO timestamp as '29 Jul 2026'. A raw ISO string in the masthead
    reads as a bug ("Palo Alto Networks — 2026-07-29T07:51:04")."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        return ""
    try:
        return datetime.fromisoformat(timestamp.strip()).strftime("%d %b %Y")
    except ValueError:
        return timestamp.strip()[:10]


def build_targets(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flatten `price_targets_yahoo` for the KPI card and the targets table.

    Adds the direction class and arrow glyph the template needs, so status
    colour always ships alongside a second, non-colour channel. `None` when the
    provider returned nothing — the whole block then disappears rather than
    rendering a table of N/A.
    """
    if not raw:
        return None
    targets = raw.get("price_targets")
    if not isinstance(targets, dict) or targets.get("mean") is None:
        return None

    out = dict(targets)
    # 2dp *strings*: round() alone renders 24.0 rather than 24.00 in a price
    # column. upside_pct_* stay numeric — the template compares them to 0.
    for key in ("mean", "median", "high", "low", "current"):
        if isinstance(out.get(key), (int, float)) and not isinstance(out.get(key), bool):
            out[key] = f"{float(out[key]):.2f}"

    upside = out.get("upside_pct_mean")
    if isinstance(upside, (int, float)) and not isinstance(upside, bool):
        out["upside_direction"] = "pos" if upside > 0 else "neg" if upside < 0 else "flat"
        out["upside_arrow"] = "▲" if upside > 0 else "▼" if upside < 0 else "→"
    else:
        out["upside_direction"] = "flat"
        out["upside_arrow"] = ""
    return out


def next_earnings_date(raw: dict[str, Any] | None) -> str | None:
    """First upcoming earnings date from `events_calendar_yahoo`, as '04 Aug'.

    'Earnings Date' holds a list because an unconfirmed date can span a window.
    """
    if not raw:
        return None
    dates = (raw.get("calendar") or {}).get("Earnings Date")
    if isinstance(dates, str):
        dates = [dates]
    if not isinstance(dates, list) or not dates:
        return None
    try:
        return datetime.fromisoformat(str(dates[0])).strftime("%d %b")
    except ValueError:
        return str(dates[0])


def map_technical(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """`technical_indicators_computed` in the shape the summary table reads.

    The artifact's own key names are the fetcher's (`rsi`, `atr`,
    `above_sma20`); the template speaks the table's language (`rsi_14`,
    `atr_14`, `above_20sma`). Mapping here keeps the translation in one place
    rather than spread across Jinja conditionals.
    """
    if not raw:
        return None
    indicators = raw.get("indicators") or {}
    signals = raw.get("trend_signals") or {}
    return {
        "latest_price": format_price(raw.get("close")),
        "as_of": raw.get("date"),
        "indicators": {
            "sma_20": indicators.get("sma_20"),
            "sma_50": indicators.get("sma_50"),
            "sma_200": indicators.get("sma_200"),
            "rsi_14": indicators.get("rsi"),
            "macd": indicators.get("macd"),
            "atr_14": indicators.get("atr"),
            "avg_volume_20d": indicators.get("volume_avg_20d"),
        },
        "trend_signals": {
            "above_20sma": signals.get("above_sma20"),
            "above_50sma": signals.get("above_sma50"),
            "above_200sma": signals.get("above_sma200"),
            "macd_bullish": signals.get("macd_bullish"),
            "sma_50_200_bullish": signals.get("golden_cross"),
        },
    }
