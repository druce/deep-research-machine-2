"""Deterministic assembly of a report run (spec §15.3, §8.2, §16.2, §24).

The rule these tests defend: `assemble` launches no model agent and invents
nothing. It recomputes the verdict arithmetic, refuses a chartbook that points
at an exhibit which does not exist, renumbers draft citations into the reference
list, and re-runs the one hard check whose failure would otherwise ship — an
internal filename in report prose.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lib.provenance import SourceMeta, StructuredMeta, write_derived, write_source, write_structured
from lib.render.assemble import assemble
from lib.render.runs import current_run, next_run
from lib.sections import SECTION_IDS, load_sections

TODAY = date(2026, 8, 11)

VERDICT = {
    "rating": "Buy", "conviction": "medium", "fair_value": 215.0,
    "horizon_months": 12, "current_price": 187.4, "implied_return_pct": 0.0,
    "valuation_method": "DCF", "thesis": "Platformization is working.",
    "key_risk": "SASE share loss.", "base_case_probability": 0.6,
    "vs_consensus": "in line",
}


# --- fixture corpus -------------------------------------------------------

def _bronze(ticker_dir: Path) -> None:
    write_source(ticker_dir, SourceMeta(
        id="2026-05-21_sec_10q", ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/x", fetched_at="2026-05-21T12:00:00+00:00",
        as_of="2026-05-20", title="PANW Q3 FY26 10-Q", fetch_tool="edgartools",
        fetch_cmd="sra.py prefetch PANW --kinds filings"), "filing text")
    write_source(ticker_dir, SourceMeta(
        id="2026-08-01_news_sase", ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://news.example.com/sase", fetched_at="2026-08-01T12:00:00+00:00",
        as_of="2026-08-01", title="SASE competition intensifies", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news"), "news text")
    write_structured(ticker_dir, StructuredMeta(
        id="profile_yahoo", ticker="PANW", producer="fetch",
        title="Palo Alto Networks, Inc. company profile", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/profile",
        provider_tool="yfinance.Ticker.info",
        fetch_cmd="sra.py prefetch PANW --kinds profile",
        fetched_at="2026-08-10T12:00:00+00:00", as_of="2026-08-10"), {
            "longName": "Palo Alto Networks, Inc.", "sector": "Technology",
            "industry": "Software — Infrastructure", "marketCap": 124_300_000_000,
            "currency": "USD"})
    write_structured(ticker_dir, StructuredMeta(
        id="prices_yahoo", ticker="PANW", producer="fetch",
        title="PANW daily OHLCV prices (4y)", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/history",
        provider_tool="yfinance.download",
        fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-08-10T12:00:00+00:00", as_of="2026-08-10"),
        {"daily": {"dates": ["2026-08-10"], "close": [187.4]}, "benchmark": None})
    write_structured(ticker_dir, StructuredMeta(
        id="technical_indicators_computed", ticker="PANW", producer="compute",
        title="PANW technical indicators", source="computed",
        provider_tool="lib/fetchers/technical.py",
        fetch_cmd="sra.py prefetch PANW --kinds technical",
        computed_at="2026-08-10T12:00:00+00:00", as_of="2026-08-10",
        derived_from=["prices_yahoo"]), {
            "close": 187.4, "date": "2026-08-10",
            "indicators": {"sma_20": 180.1, "sma_50": 176.0, "sma_200": 165.2,
                           "rsi": 61.2, "macd": 2.1, "atr": 4.4,
                           "volume_avg_20d": 5_100_000},
            "trend_signals": {"above_sma20": True, "above_sma50": True,
                              "above_sma200": True, "macd_bullish": True,
                              "golden_cross": True}})


def _run_files(run_dir: Path, *, body: str | None = None,
               conclusion: str | None = None) -> None:
    """Seven drafts opening with the exact H2 the hard checks require, so the
    assembled anchors and headings are the ones a real run produces."""
    titles = load_sections()["sections"]
    sections = run_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    for n, sid in enumerate(SECTION_IDS, start=1):
        text = body if (body is not None and n == 1) else (
            f"## {n}. {titles[sid]['title']}\n\nProse for {sid}.\n")
        (sections / f"{sid}.md").write_text(text, encoding="utf-8")
    (run_dir / "conclusion.md").write_text(
        conclusion if conclusion is not None else
        "## Conclusion: Investment Thesis\n\nWe rate PANW Buy [^2026-08-01_news_sase].\n",
        encoding="utf-8")
    (run_dir / "verdict.json").write_text(json.dumps(VERDICT), encoding="utf-8")


def _chartbook(ticker_dir: Path, entries: list[dict], *, render: bool = True) -> None:
    candidates = ticker_dir / "charts" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    if render:
        for entry in entries:
            (candidates / f"{entry['name']}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (ticker_dir / "charts" / "chartbook.json").write_text(
        json.dumps({"selected": entries}), encoding="utf-8")


@pytest.fixture
def corpus(tmp_ticker_dir: Path) -> tuple[Path, Path]:
    """A ticker with bronze, one complete run, and a one-exhibit chartbook."""
    _bronze(tmp_ticker_dir)
    run_dir = next_run(tmp_ticker_dir, TODAY)
    _run_files(run_dir, body=(
        "## 1. Company Profile\n\nPANW sells network security "
        "[^2026-05-21_sec_10q].\n"))
    _chartbook(tmp_ticker_dir, [{"name": "revenue_growth", "section": "profile",
                                 "order": 1, "caption": "Revenue by segment."}])
    return tmp_ticker_dir, run_dir


# --- happy path -----------------------------------------------------------

def test_assemble_writes_report_references_and_citation_map(corpus):
    ticker_dir, run_dir = corpus
    ok, data, err = assemble(ticker_dir, run_dir)
    assert (ok, err) == (True, None)
    assert (run_dir / "report.md").exists()
    assert (run_dir / "references.md").exists()
    assert (run_dir / "citation_map.json").exists()
    assert data["markdown"] == run_dir / "report.md"


def test_draft_citations_become_numbers_in_order_of_appearance(corpus):
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "[^1]" in report and "[^2]" in report
    assert "2026-05-21_sec_10q" not in report
    assert json.loads((run_dir / "citation_map.json").read_text(encoding="utf-8")) == {
        "1": "2026-05-21_sec_10q", "2": "2026-08-01_news_sase"}


def test_references_render_in_the_assembled_markdown(corpus):
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## References" in report
    assert "[1] PANW Q3 FY26 10-Q — SEC EDGAR, https://www.sec.gov/x" in report


def test_verdict_arithmetic_is_recomputed_not_trusted(corpus):
    """§15.3: the driver recalculates `implied_return_pct` — the fixture ships
    a deliberately wrong 0.0."""
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    verdict = json.loads((run_dir / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["implied_return_pct"] == pytest.approx(14.73, abs=0.01)
    assert "14.73%" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_sections_appear_in_report_order(corpus):
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    positions = [report.index(f"## {n}. ") for n in range(1, len(SECTION_IDS) + 1)]
    assert positions == sorted(positions)
    assert report.index("## Conclusion: Investment Thesis") > positions[-1]


def test_selected_chart_is_embedded_once_at_its_section(corpus):
    """§16.2: a selected chart is embedded at its section and nowhere else.

    It used to appear a second time in a Chartbook appendix, which made every
    exhibit a duplicate and buried the two dashboard charts under a third copy.
    """
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert report.count("charts/candidates/revenue_growth.png") == 1
    assert "## Chartbook" not in report
    assert report.index("charts/candidates/revenue_growth.png") > \
        report.index("## 1. Company Profile")


def test_chart_paths_are_relative_to_the_run_directory(corpus):
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "![](../../charts/candidates/revenue_growth.png)" in report
    assert str(ticker_dir) not in report


def test_run_stats_records_what_assembly_owns(corpus):
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    stats = json.loads((run_dir / "run_stats.json").read_text(encoding="utf-8"))
    assert stats["assemble"]["citations"] == 2
    assert stats["assemble"]["sections"] == len(SECTION_IDS)
    assert stats["assemble"]["exhibits"] == 1


def test_run_stats_written_by_earlier_phases_is_preserved(corpus):
    """The polish chain and the orchestrator write into the same file; assembly
    updates its own block rather than replacing theirs."""
    ticker_dir, run_dir = corpus
    (run_dir / "run_stats.json").write_text(
        json.dumps({"started_at": "2026-08-11T09:00:00+00:00",
                    "subagents": [{"purpose": "answerer"}]}), encoding="utf-8")
    assemble(ticker_dir, run_dir)
    stats = json.loads((run_dir / "run_stats.json").read_text(encoding="utf-8"))
    assert stats["started_at"] == "2026-08-11T09:00:00+00:00"
    assert stats["subagents"] == [{"purpose": "answerer"}]
    assert "assemble" in stats


def test_assembly_is_idempotent(corpus):
    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    first = (run_dir / "report.md").read_text(encoding="utf-8")
    assemble(ticker_dir, run_dir)
    assert (run_dir / "report.md").read_text(encoding="utf-8") == first


# --- refusals -------------------------------------------------------------

def test_chartbook_referencing_a_missing_candidate_fails(corpus):
    ticker_dir, run_dir = corpus
    _chartbook(ticker_dir, [{"name": "no_such_chart", "section": "profile",
                             "order": 1, "caption": "x"}], render=False)
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "no_such_chart" in err


def test_internal_filename_in_section_prose_fails(corpus):
    """§8.2: internal artifact names must never appear in report prose. The
    check is re-run here because a section could have been hand-edited after
    the writer passed it."""
    ticker_dir, run_dir = corpus
    (run_dir / "sections" / "profile.md").write_text(
        "## 1. Company Profile\n\nSee structured/profile_yahoo.json for detail.\n",
        encoding="utf-8")
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "no_internal_filenames" in err


def test_missing_section_file_fails(corpus):
    ticker_dir, run_dir = corpus
    (run_dir / "sections" / "valuation.md").unlink()
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "valuation" in err


def test_missing_conclusion_fails(corpus):
    ticker_dir, run_dir = corpus
    (run_dir / "conclusion.md").unlink()
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "conclusion" in err


def test_missing_verdict_fails(corpus):
    ticker_dir, run_dir = corpus
    (run_dir / "verdict.json").unlink()
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "verdict" in err


def test_unresolvable_citation_fails(corpus):
    """§8.2: "a citation that fails to resolve is a build defect"."""
    ticker_dir, run_dir = corpus
    (run_dir / "sections" / "profile.md").write_text(
        "## 1. Company Profile\n\nA claim [^never_fetched].\n", encoding="utf-8")
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "never_fetched" in err


def test_citation_to_silver_fails(corpus):
    """Silver is never a citation target (§8.1)."""
    ticker_dir, run_dir = corpus
    write_derived(ticker_dir, StructuredMeta(
        id="peers_selected", ticker="PANW", producer="model",
        title="PANW selected peer set", source="sra-rater",
        generated_at="2026-08-10T12:00:00+00:00", as_of="2026-08-10",
        derived_from=["peers_candidates"]), {"peers": []}, namespace="peers")
    (run_dir / "sections" / "profile.md").write_text(
        "## 1. Company Profile\n\nA claim [^peers_selected].\n", encoding="utf-8")
    ok, _, err = assemble(ticker_dir, run_dir)
    assert ok is False
    assert "silver" in err


def test_missing_chartbook_degrades_to_a_report_without_exhibits(corpus):
    """A chartbook is the selection skill's output, not a hard input: a report
    with no exhibits is worse but still a report."""
    ticker_dir, run_dir = corpus
    (ticker_dir / "charts" / "chartbook.json").unlink()
    ok, data, err = assemble(ticker_dir, run_dir)
    assert (ok, err) == (True, None)
    assert data["exhibits"] == 0
    assert "## Chartbook" not in (run_dir / "report.md").read_text(encoding="utf-8")


# --- the gold gate integrates with validate -------------------------------

def test_assembled_report_passes_validate(corpus, tmp_path: Path):
    from lib.validate import has_errors, validate

    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    findings = validate(ticker_dir, tmp_path / "data")
    assert not has_errors(findings), [f.message for f in findings if f.severity == "error"]


def test_a_citation_map_entry_pointing_at_silver_fails_validate(corpus, tmp_path: Path):
    """§24's assembled-report block: mapping to silver must fail."""
    from lib.validate import has_errors, validate

    ticker_dir, run_dir = corpus
    assemble(ticker_dir, run_dir)
    write_derived(ticker_dir, StructuredMeta(
        id="peers_selected", ticker="PANW", producer="model",
        title="PANW selected peer set", source="sra-rater",
        generated_at="2026-08-10T12:00:00+00:00", as_of="2026-08-10",
        derived_from=["peers_candidates"]), {"peers": []}, namespace="peers")
    (run_dir / "citation_map.json").write_text(
        json.dumps({"1": "peers_selected", "2": "2026-08-01_news_sase"}),
        encoding="utf-8")
    findings = validate(ticker_dir, tmp_path / "data")
    assert has_errors(findings)


# --- run directory resolution ---------------------------------------------

def test_current_run_is_the_newest_unsnapshotted_run(tmp_ticker_dir: Path):
    first = next_run(tmp_ticker_dir, TODAY)
    first.mkdir(parents=True)
    assert current_run(tmp_ticker_dir, TODAY) == first


def test_current_run_skips_a_snapshotted_run(tmp_ticker_dir: Path):
    """A snapshotted run is immutable — the next build gets `_2` rather than
    writing over §24's diff target."""
    first = next_run(tmp_ticker_dir, TODAY)
    first.mkdir(parents=True)
    (first / "snapshot.json").write_text("{}", encoding="utf-8")
    assert current_run(tmp_ticker_dir, TODAY).name == "2026-08-11_2"


def test_run_ordering_is_numeric_not_lexical(tmp_ticker_dir: Path):
    reports = tmp_ticker_dir / "reports"
    for name in ("2026-08-11", "2026-08-11_2", "2026-08-11_10"):
        (reports / name).mkdir(parents=True)
        (reports / name / "snapshot.json").write_text("{}", encoding="utf-8")
    assert current_run(tmp_ticker_dir, TODAY).name == "2026-08-11_11"


# --- the CLI surface ------------------------------------------------------

def _cli_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """An initialized ticker under a real data root, ready to assemble."""
    import sra

    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    ticker_dir = tmp_path / "PANW"
    _bronze(ticker_dir)
    run_dir = next_run(ticker_dir, TODAY)
    _run_files(run_dir, body=("## 1. Company Profile\n\nPANW sells network "
                              "security [^2026-05-21_sec_10q].\n"))
    _chartbook(ticker_dir, [{"name": "revenue_growth", "section": "profile",
                             "order": 1, "caption": "Revenue by segment."}])
    return ticker_dir, run_dir


def test_cli_assemble_exits_zero_and_writes_the_report(tmp_path: Path, capsys):
    import sra

    ticker_dir, run_dir = _cli_corpus(tmp_path)
    capsys.readouterr()                      # drop `init`'s own line
    code = sra.main(["assemble", "PANW", "--data-root", str(tmp_path),
                     "--run", run_dir.name])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["citations"] == 2 and out["exhibits"] == 1
    assert (run_dir / "report.md").exists()


def test_cli_assemble_defaults_to_the_current_run(tmp_path: Path, capsys, monkeypatch):
    import sra

    ticker_dir, run_dir = _cli_corpus(tmp_path)
    capsys.readouterr()                      # drop `init`'s own line
    monkeypatch.setattr(
        sra, "_utcnow",
        lambda: __import__("datetime").datetime(2026, 8, 11, tzinfo=
                                                __import__("datetime").timezone.utc))
    assert sra.main(["assemble", "PANW", "--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["run"] == run_dir.name


def test_cli_assemble_exits_one_on_a_dangling_exhibit(tmp_path: Path, capsys):
    import sra

    ticker_dir, run_dir = _cli_corpus(tmp_path)
    _chartbook(ticker_dir, [{"name": "gone", "section": "profile", "order": 1,
                             "caption": "x"}], render=False)
    assert sra.main(["assemble", "PANW", "--data-root", str(tmp_path),
                     "--run", run_dir.name]) == 1
    assert "gone" in capsys.readouterr().err


def test_cli_assemble_exits_one_when_the_run_does_not_exist(tmp_path: Path, capsys):
    import sra

    _cli_corpus(tmp_path)
    assert sra.main(["assemble", "PANW", "--data-root", str(tmp_path),
                     "--run", "2020-01-01"]) == 1
    assert "no report run" in capsys.readouterr().err
