"""The report template and the markdown -> HTML -> PDF chain (spec §15.3).

The offline tests assert the structure of the generated markdown: the masthead,
the verdict card, the KPI strip, the body handed over verbatim, the chartbook
appendix and the reference list. The pandoc/weasyprint invocations are marked
`integration` — they shell out to binaries that are not present everywhere.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.render.assemble import (
    CSS_NAME, TEMPLATES_DIR, render_markdown, to_html, to_pdf,
)


def _variables(**overrides) -> dict:
    base = {
        "symbol": "PANW",
        "company_name": "Palo Alto Networks, Inc.",
        "company_name_short": "Palo Alto Networks",
        "sector": "Technology",
        "industry": "Software — Infrastructure",
        "report_date": "11 Aug 2026",
        "latest_price": "187.40",
        "market_cap": "124.3B",
        "trailing_pe": "48.2",
        "forward_pe": "52.1",
        "verdict": {
            "rating": "Buy", "conviction": "medium", "fair_value": 215.0,
            "horizon_months": 12, "current_price": 187.4,
            "implied_return_pct": 14.73, "return_direction": "pos",
            "valuation_method": "DCF and forward EV/S", "vs_consensus": "in line",
            "thesis": "Platformization is converting point products into contracts.",
            "key_risk": "SASE share loss to Zscaler.",
            "base_case_probability": 0.6,
        },
        "technical_analysis": {
            "indicators": {"sma_20": 180.1, "sma_50": 176.0, "sma_200": 165.2,
                           "rsi_14": 61.2, "macd": 2.1, "atr_14": 4.4,
                           "avg_volume_20d": 5_100_000},
            "trend_signals": {"above_20sma": True, "above_50sma": True,
                              "above_200sma": True, "macd_bullish": True,
                              "sma_50_200_bullish": True},
        },
        "peers": [
            {"symbol": "PANW", "name": "Palo Alto Networks", "price": "187.40",
             "market_cap": "124.3B", "forward_pe": "52.1", "revenue_ttm": "8.0B",
             "operating_margin": "12.4%", "revenue_growth": "14.2%",
             "is_subject": True},
            {"symbol": "FTNT", "name": "Fortinet", "price": "84.10",
             "market_cap": "64.5B", "forward_pe": "38.4", "revenue_ttm": "5.9B",
             "operating_margin": "28.1%", "revenue_growth": "11.0%",
             "is_subject": False},
        ],
        "peer_caption": "Forward P/E · Revenue TTM · operating margin. Subject shaded.",
        "targets": {"mean": "205.00", "median": "203.00", "low": "150.00",
                    "high": "260.00", "upside_pct_mean": 9.4,
                    "upside_pct_low": -19.9, "upside_pct_median": 8.3,
                    "upside_pct_high": 38.7, "upside_direction": "pos",
                    "upside_arrow": "▲"},
        "next_earnings": "04 Aug",
        "chart_path": "../../charts/candidates/price_weekly.png",
        "chart_caption": "4-year weekly price, moving averages and relative strength.",
        "income_statement_sankey_path": "../../charts/candidates/income_sankey.png",
        "sankey_caption": "Revenue flow to net income, FY2026.",
        "toc_sections": [
            {"anchor": "1-company-profile", "title": "Company Profile"},
            {"anchor": "2-business-model", "title": "Business Model"},
        ],
        "body": "## 1. Company Profile\n\nPANW sells network security [^1].\n",
        "conclusion": "## Conclusion: Investment Thesis\n\nWe rate PANW Buy [^2].\n",
        "chartbook": [
            {"name": "revenue_growth", "path": "../../charts/candidates/revenue_growth.png",
             "caption": "Revenue growth by segment — Yahoo Finance, as of 2026-06-30"},
        ],
        "references": ("## References\n\n"
                       "[1] PANW FY26 10-K — SEC EDGAR, https://www.sec.gov/x — fetched 2026-05-21\n"),
    }
    base.update(overrides)
    return base


# --- structure ------------------------------------------------------------

def test_masthead_carries_company_ticker_and_date():
    md = render_markdown(_variables())
    assert "Palo Alto Networks, Inc." in md
    assert 'class="masthead-tkr">PANW<' in md
    assert "Equity Research · 11 Aug 2026" in md


def test_verdict_card_renders_rating_and_implied_return():
    md = render_markdown(_variables())
    assert "## Investment Verdict" in md
    assert "::: {.verdict-card}" in md
    assert '<span class="chip pos">14.73%</span>' in md
    assert "**Thesis** Platformization" in md


def test_verdict_absent_drops_the_whole_card():
    md = render_markdown(_variables(verdict=None))
    assert "## Investment Verdict" not in md
    assert "masthead-verdict" not in md


def test_base_case_probability_renders_as_a_percentage():
    md = render_markdown(_variables())
    assert "| **Base case probability** | 60% |" in md


def test_kpi_strip_prefers_forward_pe():
    md = render_markdown(_variables())
    assert "Fwd P/E" in md
    assert "Trailing P/E" not in md


def test_kpi_strip_falls_back_to_trailing_pe():
    md = render_markdown(_variables(forward_pe="N/A"))
    assert "Trailing P/E" in md


def test_body_and_conclusion_are_passed_through_verbatim():
    """Sections already open with their own `## N. Title`; the template must
    not wrap or re-head them."""
    md = render_markdown(_variables())
    assert ("## 1. Company Profile\n\nPANW sells network security "
            '<sup class="cite"><a id="cite-1-1" href="#ref-1">1</a></sup>.') in md
    assert "## Conclusion: Investment Thesis" in md
    assert md.count("## 1. Company Profile") == 1


def test_technical_table_marks_a_missing_indicator_as_no_data():
    """§6.4: a value the provider did not report is absent, not zero."""
    variables = _variables()
    variables["technical_analysis"]["indicators"]["sma_200"] = None
    md = render_markdown(variables)
    assert "insufficient trading history for a 200-day SMA" in md


def test_peer_table_bolds_the_subject_row():
    md = render_markdown(_variables())
    assert "| **PANW** | **Palo Alto Networks** |" in md
    assert "| FTNT | Fortinet |" in md


def test_absent_peers_degrade_to_a_note():
    md = render_markdown(_variables(peers=[]))
    assert "*Peer comparison data not available*" in md


def test_absent_chart_degrades_to_a_note():
    md = render_markdown(_variables(chart_path=None))
    assert "*Stock chart not available*" in md


def test_the_template_renders_no_chartbook_appendix():
    """The gallery repeated every exhibit at full size — a verbatim second copy
    of what the body already shows beside the argument it supports."""
    md = render_markdown(_variables())
    assert "## Chartbook" not in md
    assert 'href="#chartbook"' not in md


def test_references_section_is_included():
    md = render_markdown(_variables())
    assert "## References" in md
    assert ('<span class="ref-n" id="ref-1">[1]</span> PANW FY26 10-K — '
            "SEC EDGAR") in md


def test_sources_and_methodology_describes_the_sra6_pipeline():
    """The methodology block must not describe sra5's retired vector index."""
    md = render_markdown(_variables())
    assert "## Sources and Methodology" in md
    assert "vector" not in md.lower()


def test_images_are_emitted_with_empty_alt():
    """A non-empty alt makes pandoc emit a <figcaption> duplicating the
    caption below the figure."""
    md = render_markdown(_variables())
    assert "![](../../charts/candidates/price_weekly.png)" in md
    assert "![Exhibit" not in md


def test_body_tables_are_realigned_before_pandoc():
    body = ("## 5. Financial Strength\n\n"
            "| Metric | FY25 | FY26 |\n"
            "|---|---|---|\n"
            "| Revenue | 8.0 | 9.2 |\n")
    md = render_markdown(_variables(body=body))
    assert "|:---|---:|---:|" in md


def test_template_and_css_ship_together():
    assert (TEMPLATES_DIR / CSS_NAME).exists()
    assert (TEMPLATES_DIR / CSS_NAME).read_text(encoding="utf-8").startswith("<style>")


# --- the pandoc / weasyprint chain ----------------------------------------

@pytest.mark.integration
def test_pandoc_produces_standalone_html_with_one_h1(tmp_path: Path):
    md_path = tmp_path / "report.md"
    md_path.write_text(render_markdown(_variables()), encoding="utf-8")
    html_path = tmp_path / "report.html"

    assert to_html(md_path, html_path, pagetitle="PANW Equity Research") is None
    html = html_path.read_text(encoding="utf-8")
    assert "<title>PANW Equity Research</title>" in html
    assert html.count("<h1") == 1          # the masthead's, not a pandoc title block


@pytest.mark.integration
def test_weasyprint_produces_a_pdf(tmp_path: Path):
    md_path = tmp_path / "report.md"
    md_path.write_text(render_markdown(_variables()), encoding="utf-8")
    html_path = tmp_path / "report.html"
    pdf_path = tmp_path / "report.pdf"

    assert to_html(md_path, html_path, pagetitle="PANW Equity Research") is None
    assert to_pdf(html_path, pdf_path) is None
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_missing_pandoc_degrades_to_an_error_string(tmp_path: Path, monkeypatch):
    """§22.3: a render failure is reported, not raised — the assembled markdown
    is already on disk and must not be lost to a missing binary."""
    import subprocess

    def boom(*args, **kwargs):
        raise FileNotFoundError("pandoc")

    monkeypatch.setattr(subprocess, "run", boom)
    md_path = tmp_path / "report.md"
    md_path.write_text("# x\n", encoding="utf-8")
    error = to_html(md_path, tmp_path / "report.html", pagetitle="x")
    assert error is not None and "pandoc not found" in error
