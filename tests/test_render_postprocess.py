"""Markdown post-processing and template value formatting (spec §15.3).

Ported from SRA5's `tests/test_render_final_helpers.py` — §15.3 requires the
sra5 rendering fixes be reused rather than reinvented, so their behavior is
pinned here before the SRA6 render chain depends on it.

The rule these tests defend: pandoc reads structure, not intent. A separator row
without alignment markers rags every numeric column left no matter what the
stylesheet says, and a non-empty image alt becomes a second caption.
"""
from __future__ import annotations

from lib.render.postprocess import (
    align_numeric_columns, blank_image_alts, build_targets, colour_signed_cells,
    format_market_cap, format_price, format_report_date, map_technical,
    mark_scenario_tables, next_earnings_date, postprocess, shorten_company_name,
)


def _sep(markdown: str) -> str:
    """The separator row of the first table."""
    return [ln for ln in markdown.split("\n") if set(ln) <= set("|-: ") and "-" in ln][0]


# --- align_numeric_columns ------------------------------------------------

def test_rewrites_model_written_separator():
    md = (
        "| Product line | FY2025 revenue | % of total | YoY |\n"
        "|---|---|---|---|\n"
        "| Subscription | $12.0B | 36.7% | -6.0% |\n"
        "| Product | $11.6B | 35.5% | +39.0% |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|---:|---:|"


def test_first_column_stays_left_even_when_numeric():
    """Column 0 is the row label even when it parses as a number ("2024")."""
    md = (
        "| Year | Revenue |\n"
        "|---|---|\n"
        "| 2024 | 28.3 |\n"
        "| 2025 | 32.7 |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|"


def test_explicit_alignment_is_left_untouched():
    """Alignment set deliberately in final_report.md.j2 always wins."""
    md = (
        "| Indicator | Value | Signal |\n"
        "|:----------|------:|:-------|\n"
        "| MACD | -19.58 | Bearish |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:----------|------:|:-------|"


def test_text_columns_are_not_right_aligned():
    md = (
        "| Market | Share | Source |\n"
        "|---|---|---|\n"
        "| NGFW | 100% | Sole commercial supplier |\n"
        "| SASE | 94.1% | Zscaler 3.4%, Netskope 2.5% (Gartner, 2025) |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|:---|"


def test_table_with_no_numeric_columns_is_untouched():
    md = (
        "| Source | Used for |\n"
        "|---|---|\n"
        "| SEC EDGAR | Reported figures |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|---|---|"


def test_na_cells_do_not_veto_a_numeric_column():
    md = (
        "| Peer | P/E |\n"
        "|---|---|\n"
        "| FTNT | 44.8 |\n"
        "| ZS | N/A |\n"
        "| CRWD | 54.1 |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|"


def test_one_prose_cell_does_not_veto_a_mostly_numeric_column():
    """"55-57% (guidance)" among five clean percentages still right-aligns."""
    md = (
        "| Metric | FY22 | FY23 | FY24 | FY25 | Q3E |\n"
        "|---|---|---|---|---|---|\n"
        "| Gross margin | 50.5% | 51.3% | 51.3% | 52.8% | 55-57% (guidance) |\n"
        "| Operating margin | 30.7% | 32.5% | 30.7% | 34.2% | 36% (guidance) |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|---:|---:|---:|:---|"


def test_currency_percent_parens_and_scale_suffixes_parse_as_numeric():
    md = (
        "| Line | A | B | C | D |\n"
        "|---|---|---|---|---|\n"
        "| row | $1,582.95 | (2.4) | $12.0B | 54.6x |\n"
        "| row | $1,392.20 | (0.8) | $11.6B | 27.4x |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|---:|---:|---:|"


def test_cells_wrapped_in_markup_still_parse():
    """Bold totals and inline chips must not defeat detection."""
    md = (
        "| Line | Amount |\n"
        "|---|---|\n"
        "| Capex | $1.63B |\n"
        "| **Total** | **$32.7B** |\n"
        "| Tagged | <span class=\"chip pos\">12.5</span> |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|:---|---:|"


def test_tables_inside_code_fences_are_ignored():
    md = (
        "```\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
        "```\n"
    )
    assert align_numeric_columns(md) == md


def test_multiple_tables_are_each_handled():
    md = (
        "| Peer | P/E |\n"
        "|---|---|\n"
        "| FTNT | 44.8 |\n"
        "\n"
        "| Source | Used for |\n"
        "|---|---|\n"
        "| EDGAR | Filings |\n"
    )
    out = align_numeric_columns(md).split("\n")
    assert out[1] == "|:---|---:|"     # numeric table rewritten
    assert out[5] == "|---|---|"       # prose table untouched


def test_dates_are_not_numeric():
    md = (
        "| Event | Date |\n"
        "|---|---|\n"
        "| Earnings | 2026-08-04 |\n"
        "| Dividend | 2026-09-15 |\n"
    )
    assert _sep(align_numeric_columns(md)) == "|---|---|"


def test_prose_without_tables_is_unchanged():
    md = "# Heading\n\nSome prose with a | pipe in it.\n"
    assert align_numeric_columns(md) == md


# --- mark_scenario_tables -------------------------------------------------

def test_bear_base_bull_table_is_wrapped_with_the_base_column_marked():
    md = (
        "| Scenario | Bear | Base | Bull |\n"
        "|---|---|---|---|\n"
        "| Fair value | $120 | $185 | $240 |\n"
    )
    out = mark_scenario_tables(md).split("\n")
    assert out[0] == "::: {.scenario .base-3}"
    assert out[4] == ":::"          # opener, header, separator, one body row, closer


def test_an_ordinary_three_column_table_is_not_captured():
    md = (
        "| Metric | Value | Source |\n"
        "|---|---|---|\n"
        "| Revenue | $8.0B | 10-K |\n"
    )
    assert ".scenario" not in mark_scenario_tables(md)


def test_a_table_missing_the_wings_is_not_captured():
    md = (
        "| Case | Base | Upside |\n"
        "|---|---|---|\n"
        "| FV | $185 | $240 |\n"
    )
    assert ".scenario" not in mark_scenario_tables(md)


# --- colour_signed_cells --------------------------------------------------

def test_whole_cell_signed_numbers_are_wrapped():
    md = (
        "| Metric | YoY |\n"
        "|---|---|\n"
        "| Revenue | +14.2% |\n"
        "| Billings | -3.0% |\n"
    )
    out = colour_signed_cells(md)
    assert '<span class="num pos">+14.2%</span>' in out
    assert '<span class="num neg">-3.0%</span>' in out


def test_a_signed_number_inside_a_sentence_is_left_alone():
    md = (
        "| Metric | Note |\n"
        "|---|---|\n"
        "| Revenue | grew +14.2% on renewals |\n"
    )
    assert "<span" not in colour_signed_cells(md)


def test_a_cell_that_already_carries_html_is_left_alone():
    md = (
        "| Metric | YoY |\n"
        "|---|---|\n"
        "| Revenue | <span class=\"chip pos\">+14.2%</span> |\n"
    )
    assert colour_signed_cells(md).count("<span") == 1


def test_separator_rows_survive_the_signed_cell_pass():
    md = (
        "| Metric | YoY |\n"
        "|:---|---:|\n"
        "| Revenue | +14.2% |\n"
    )
    assert "|:---|---:|" in colour_signed_cells(md)


# --- blank_image_alts -----------------------------------------------------

def test_non_empty_alt_is_blanked():
    """A non-empty alt makes pandoc emit a <figcaption> that duplicates the
    caption the template already wrote under the exhibit."""
    md = "![Revenue growth by segment](charts/candidates/revenue_growth.png)"
    assert blank_image_alts(md) == "![](charts/candidates/revenue_growth.png)"


def test_empty_alt_is_preserved():
    md = "![](charts/candidates/price_weekly.png)"
    assert blank_image_alts(md) == md


def test_link_text_is_not_treated_as_an_image():
    md = "See [the filing](https://www.sec.gov/x)."
    assert blank_image_alts(md) == md


# --- postprocess ordering -------------------------------------------------

def test_alignment_runs_before_the_signed_cell_pass():
    """Wrapping cells in HTML first would hide the raw numbers from the
    alignment vote, and every numeric column would rag left."""
    md = (
        "| Metric | YoY |\n"
        "|---|---|\n"
        "| Revenue | +14.2% |\n"
        "| Billings | -3.0% |\n"
    )
    out = postprocess(md)
    assert "|:---|---:|" in out
    assert '<span class="num pos">+14.2%</span>' in out


# --- value formatting -----------------------------------------------------

def test_format_price_rounds_and_degrades():
    assert format_price(114.68000030517578) == "114.68"
    assert format_price(None) == "N/A"
    assert format_price("nope") == "N/A"


def test_format_market_cap_scales():
    assert format_market_cap(2_310_000_000_000) == "2.31T"
    assert format_market_cap(48_700_000_000) == "48.7B"
    assert format_market_cap(912_400_000) == "912.4M"
    assert format_market_cap(None) == "N/A"


def test_strips_one_trailing_corporate_suffix():
    assert shorten_company_name("Palo Alto Networks, Inc.") == "Palo Alto Networks"
    assert shorten_company_name("Lam Research Corporation") == "Lam Research"


def test_strips_only_one_suffix():
    """A second pass would reduce "ASML Holding N.V." to a bare ticker."""
    assert shorten_company_name("ASML Holding N.V.") == "ASML Holding"


def test_name_without_a_suffix_is_preserved():
    assert shorten_company_name("Toast") == "Toast"


def test_overlong_name_is_elided():
    out = shorten_company_name("Taiwan Semiconductor Manufacturing Company", max_len=20)
    assert len(out) <= 20 and out.endswith("…")


def test_missing_name_degrades_to_na():
    assert shorten_company_name(None) == "N/A"
    assert shorten_company_name("   ") == "N/A"


def test_formats_iso_timestamp():
    assert format_report_date("2026-07-29T07:51:04") == "29 Jul 2026"


def test_unparseable_timestamp_falls_back_to_date_prefix():
    assert format_report_date("2026-07-29 garbage") == "2026-07-29"


def test_missing_timestamp_is_empty():
    assert format_report_date(None) == ""
    assert format_report_date("") == ""


# --- build_targets --------------------------------------------------------

def test_absent_price_targets_returns_none():
    assert build_targets(None) is None
    assert build_targets({}) is None
    assert build_targets({"price_targets": {"mean": None}}) is None


def test_positive_upside_gets_pos_class_and_up_arrow():
    out = build_targets({"price_targets": {"mean": 34.73077, "upside_pct_mean": 6.2}})
    assert out["mean"] == "34.73"
    assert out["upside_direction"] == "pos"
    assert out["upside_arrow"] == "▲"


def test_whole_number_targets_keep_two_decimals():
    """A price column must read $24.00, not $24.0."""
    out = build_targets({"price_targets": {"mean": 34.0, "low": 24.0, "high": 45.0}})
    assert (out["mean"], out["low"], out["high"]) == ("34.00", "24.00", "45.00")


def test_upside_percentages_stay_numeric_for_template_comparisons():
    out = build_targets({"price_targets": {"mean": 20.0, "upside_pct_low": -26.6}})
    assert isinstance(out["upside_pct_low"], float)


def test_negative_upside_gets_neg_class_and_down_arrow():
    out = build_targets({"price_targets": {"mean": 20.0, "upside_pct_mean": -8.4}})
    assert out["upside_direction"] == "neg"
    assert out["upside_arrow"] == "▼"


def test_missing_upside_is_flat_with_no_arrow():
    out = build_targets({"price_targets": {"mean": 20.0}})
    assert out["upside_direction"] == "flat"
    assert out["upside_arrow"] == ""


# --- next_earnings_date ---------------------------------------------------

def test_earnings_date_list_is_formatted():
    assert next_earnings_date({"calendar": {"Earnings Date": ["2026-08-04"]}}) == "04 Aug"


def test_earnings_date_bare_string_is_accepted():
    assert next_earnings_date({"calendar": {"Earnings Date": "2026-08-04"}}) == "04 Aug"


def test_absent_calendar_returns_none():
    assert next_earnings_date(None) is None
    assert next_earnings_date({}) is None
    assert next_earnings_date({"calendar": {"Earnings Date": []}}) is None


# --- map_technical --------------------------------------------------------

def test_map_technical_translates_fetcher_keys_to_table_keys():
    out = map_technical({
        "close": 187.4,
        "date": "2026-08-10",
        "indicators": {"sma_20": 180.1, "rsi": 61.2, "atr": 4.4,
                       "volume_avg_20d": 5_100_000},
        "trend_signals": {"above_sma20": True, "golden_cross": True},
    })
    assert out["latest_price"] == "187.40"
    assert out["indicators"]["rsi_14"] == 61.2
    assert out["indicators"]["atr_14"] == 4.4
    assert out["indicators"]["avg_volume_20d"] == 5_100_000
    assert out["trend_signals"]["above_20sma"] is True
    assert out["trend_signals"]["sma_50_200_bullish"] is True


def test_map_technical_missing_indicator_stays_none():
    """§6.4: a value the provider did not supply is absent, not zero — the
    table renders "No data" rather than a fabricated 0.00."""
    out = map_technical({"close": 187.4, "indicators": {}, "trend_signals": {}})
    assert out["indicators"]["sma_200"] is None


def test_map_technical_of_nothing_is_none():
    assert map_technical(None) is None
    assert map_technical({}) is None
