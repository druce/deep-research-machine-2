"""Exhibit placement: each chart lands in the report exactly once."""

from __future__ import annotations

import json
from pathlib import Path

from lib.render.assemble import (
    DASHBOARD_CHARTS, _body, load_chartbook, peer_table_warnings,
)
from lib.validate import validate


def _ticker_dir(tmp_path: Path, selections: list[dict]) -> Path:
    d = tmp_path / "TEST"
    candidates = d / "charts" / "candidates"
    candidates.mkdir(parents=True)
    for name in {"price_weekly", "income_sankey", "revenue_growth"}:
        (candidates / f"{name}.png").write_bytes(b"\x89PNG")
    (d / "charts" / "chartbook.json").write_text(
        json.dumps({"selected": selections}), encoding="utf-8")
    return d


def test_dashboard_chart_is_dropped_with_a_warning(tmp_path: Path) -> None:
    d = _ticker_dir(tmp_path, [
        {"name": "price_weekly", "section": "profile", "order": 1, "caption": "c"},
        {"name": "revenue_growth", "section": "financial", "order": 2, "caption": "c"},
    ])
    exhibits, problems, warnings = load_chartbook(d)

    assert problems == []
    assert [e["name"] for e in exhibits] == ["revenue_growth"]
    assert any("price_weekly" in w for w in warnings)


def test_dropping_a_dashboard_chart_does_not_leave_a_numbering_gap(tmp_path: Path) -> None:
    d = _ticker_dir(tmp_path, [
        {"name": "income_sankey", "section": "business_model", "order": 1, "caption": "c"},
        {"name": "revenue_growth", "section": "financial", "order": 2, "caption": "c"},
    ])
    exhibits, _, _ = load_chartbook(d)

    assert [e["number"] for e in exhibits] == [1]


def test_body_embeds_each_image_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    png = tmp_path / "TEST" / "charts" / "candidates" / "revenue_growth.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG")

    exhibit = {"name": "revenue_growth", "section": "financial", "order": 1.0,
               "caption": "c", "png": png, "number": 1}
    drafts = {sid: f"## {sid}\n" for sid in
              ("profile", "business_model", "competitive", "financial",
               "supply_chain", "valuation", "risk_news")}

    markdown, warnings = _body(drafts, [exhibit, dict(exhibit, number=2)], run_dir)

    assert markdown.count("revenue_growth.png") == 1
    assert len(warnings) == 1


def test_dashboard_charts_names_the_two_template_placed_charts() -> None:
    assert DASHBOARD_CHARTS == frozenset({"price_weekly", "income_sankey"})


def _report_tree(tmp_path: Path, body: str) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    d = data_root / "TEST"
    run = d / "reports" / "2026-08-12"
    run.mkdir(parents=True)
    (run / "report.md").write_text(body, encoding="utf-8")
    (run / "citation_map.json").write_text("{}", encoding="utf-8")
    return d, data_root


def test_repeated_image_is_a_fatal_finding(tmp_path: Path) -> None:
    d, data_root = _report_tree(tmp_path, (
        "![](../../charts/candidates/income_sankey.png)\n\n"
        "text\n\n"
        "![](../../charts/candidates/income_sankey.png)\n"))

    findings = [f for f in validate(d, data_root) if f.code == "exhibit-duplicated"]

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "income_sankey.png" in findings[0].message


def test_distinct_images_produce_no_finding(tmp_path: Path) -> None:
    d, data_root = _report_tree(tmp_path, (
        "![](../../charts/candidates/income_sankey.png)\n\n"
        "![](../../charts/candidates/price_weekly.png)\n"))

    assert [f for f in validate(d, data_root) if f.code == "exhibit-duplicated"] == []


def test_dangling_citation_marker_is_fatal(tmp_path: Path) -> None:
    d, data_root = _report_tree(
        tmp_path, "A claim.[^7]\n\n## References\n\n[1] Only source\n")

    findings = [f for f in validate(d, data_root) if f.code == "citation-unlinked"]

    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_linked_citations_produce_no_finding(tmp_path: Path) -> None:
    d, data_root = _report_tree(tmp_path, (
        'A claim.<sup class="cite"><a id="cite-1-1" href="#ref-1">1</a></sup>\n\n'
        "## References\n\n"
        '<span class="ref-n" id="ref-1">[1]</span> Only source\n'))

    assert [f for f in validate(d, data_root) if f.code == "citation-unlinked"] == []


def test_mostly_empty_peer_table_warns() -> None:
    peers = [{"symbol": "SUBJ", "is_subject": True, "forward_pe": "18.0",
              "revenue_ttm": "$9.0B", "operating_margin": "12.0%",
              "revenue_growth": "8.0%"}]
    peers += [{"symbol": s, "is_subject": False, "forward_pe": "N/A",
               "revenue_ttm": "N/A", "operating_margin": "N/A",
               "revenue_growth": "N/A"} for s in ("BA", "LMT", "RTX")]

    warnings = peer_table_warnings(peers)

    assert len(warnings) == 1
    assert "prefetch-peers" in warnings[0]


def test_populated_peer_table_does_not_warn() -> None:
    peers = [{"symbol": s, "is_subject": s == "SUBJ", "forward_pe": "18.0",
              "revenue_ttm": "$9.0B", "operating_margin": "12.0%",
              "revenue_growth": "8.0%"} for s in ("SUBJ", "BA", "LMT")]

    assert peer_table_warnings(peers) == []


def test_a_table_with_no_comparables_does_not_warn() -> None:
    assert peer_table_warnings([{"symbol": "SUBJ", "is_subject": True}]) == []
