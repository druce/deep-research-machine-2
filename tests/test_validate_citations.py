"""Citation and derivation resolution in the fatal gate (spec §8.1, §8.2, §8.4
checks 4 and 5).

The rule these tests defend: every citation id resolves to BRONZE evidence, and
silver is never a citation target. A report may depend on a wiki page; it may
not cite one as evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from lib.provenance import (
    SourceMeta, StructuredMeta, write_derived, write_source, write_structured,
)
from lib.validate import Finding, validate


def _source_meta(source_id: str, *, ticker: str = "PANW",
                 supersedes: str | None = None) -> SourceMeta:
    return SourceMeta(
        id=source_id, ticker=ticker, kind="news", source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of="2026-07-30", title="A headline", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news", supersedes=supersedes,
    )


def _fetch_meta(artifact_id: str, *, ticker: str = "PANW",
                derived_from: list[str] | None = None) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker=ticker, producer="fetch", title="Prices",
        source="yahoo", url="https://example.com/p", as_of="2026-07-30",
        provider_tool="yfinance", fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-07-30T12:00:00+00:00", derived_from=derived_from or [],
    )


def _model_meta(artifact_id: str, derived_from: list[str]) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker="PANW", producer="model", title="Ranked peers",
        source="sra-rater", as_of="2026-07-30",
        generated_at="2026-07-30T12:00:00+00:00", derived_from=derived_from,
    )


def _write_wiki(ticker_dir: Path, name: str, body: str, metadata: dict | None = None) -> Path:
    path = ticker_dir / "wiki" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if metadata is not None:
        lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in metadata.items()] + ["---", ""]
    path.write_text("\n".join(lines + [body, ""]), encoding="utf-8")
    return path


def _codes(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings if f.severity == "error"}


def _run(ticker_dir: Path) -> list[Finding]:
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- check 4: citations resolve to bronze --------------------------------

def test_a_citation_to_a_current_source_resolves(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "Revenue grew.")
    _write_wiki(tmp_ticker_dir, "competitive", "Revenue grew.[^2026-07-30_news_yahoo]")
    assert _run(tmp_ticker_dir) == []


def test_a_citation_to_structured_bronze_resolves(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, _fetch_meta("prices_yahoo"), {"close": [1]})
    _write_wiki(tmp_ticker_dir, "valuation", "It trades at 30x.[^prices_yahoo]")
    assert _run(tmp_ticker_dir) == []


def test_a_citation_to_an_archived_source_resolves(tmp_ticker_dir: Path):
    """§5/§8.4: citations resolve to bronze "current or archived". Superseding
    a document must not retroactively break every page that cited it."""
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "old")
    write_source(tmp_ticker_dir,
                 _source_meta("2026-07-31_news_yahoo",
                              supersedes="2026-07-30_news_yahoo"), "new")
    _write_wiki(tmp_ticker_dir, "competitive", "Claim.[^2026-07-30_news_yahoo]")
    assert _run(tmp_ticker_dir) == []


def test_an_unresolvable_citation_is_an_error(tmp_ticker_dir: Path):
    _write_wiki(tmp_ticker_dir, "competitive", "Claim.[^no_such_source]")
    findings = _run(tmp_ticker_dir)
    assert "citation-unresolved" in _codes(findings)
    assert any("no_such_source" in f.message for f in findings)


def test_citing_silver_is_an_error(tmp_ticker_dir: Path):
    """§8.1: silver artifacts are never citation targets. This is the rule that
    keeps a report citation from terminating at model-generated text."""
    write_derived(tmp_ticker_dir, _model_meta("peers_selected", ["peers_candidates"]),
                  {"peers": ["CRWD"]}, namespace="peers")
    _write_wiki(tmp_ticker_dir, "competitive", "Its peers are CRWD.[^peers_selected]")
    findings = _run(tmp_ticker_dir)
    assert "citation-unresolved" in _codes(findings)
    assert any("silver" in f.message.lower() for f in findings)


def test_citing_a_research_answer_is_an_error(tmp_ticker_dir: Path):
    """§8.1: "a researcher may read an earlier answer, but any inherited claim
    must be cited back to bronze evidence"."""
    answers = tmp_ticker_dir / "derived" / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / "2026-07-30_answer_moat.json").write_text(
        json.dumps({"_meta": {"id": "2026-07-30_answer_moat", "ticker": "PANW",
                              "producer": "model", "title": "Moat",
                              "source": "sra-researcher", "as_of": "2026-07-30",
                              "generated_at": "2026-07-30T12:00:00+00:00",
                              "derived_from": ["2026-07-30_news_yahoo"]},
                    "data": {}}), encoding="utf-8")
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "body")
    _write_wiki(tmp_ticker_dir, "competitive", "Moat is wide.[^2026-07-30_answer_moat]")
    assert "citation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_a_macro_citation_resolves(tmp_ticker_dir: Path):
    """§12: ticker citation resolution checks ticker bronze, then _MACRO
    bronze — shared macro evidence is cited from a ticker's pages."""
    macro = tmp_ticker_dir.parent / "_MACRO"
    (macro / "structured").mkdir(parents=True, exist_ok=True)
    (macro / "sources").mkdir(parents=True, exist_ok=True)
    write_structured(macro, _fetch_meta("fred_dgs10", ticker="_MACRO"), {"obs": [4.2]})
    _write_wiki(tmp_ticker_dir, "valuation", "The 10Y is 4.2%.[^fred_dgs10]")
    assert _run(tmp_ticker_dir) == []


def test_citations_in_report_section_drafts_are_checked(tmp_ticker_dir: Path):
    """§8.2: section drafts retain bronze ids directly."""
    sections = tmp_ticker_dir / "reports" / "2026-08-11" / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "competitive.md").write_text("Claim.[^no_such_source]\n", encoding="utf-8")
    assert "citation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_entity_pages_are_checked(tmp_ticker_dir: Path):
    (tmp_ticker_dir / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (tmp_ticker_dir / "wiki" / "entities" / "crwd.md").write_text(
        "CrowdStrike competes.[^no_such_source]\n", encoding="utf-8")
    assert "citation-unresolved" in _codes(_run(tmp_ticker_dir))


# --- check 4, gold half: numeric citations through citation_map.json -----

def _report(ticker_dir: Path, body: str, citation_map: dict | None) -> Path:
    run = ticker_dir / "reports" / "2026-08-11"
    run.mkdir(parents=True, exist_ok=True)
    (run / "report.md").write_text(body, encoding="utf-8")
    if citation_map is not None:
        (run / "citation_map.json").write_text(json.dumps(citation_map), encoding="utf-8")
    return run


def test_a_mapped_numeric_citation_resolves(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "body")
    _report(tmp_ticker_dir, "Revenue grew.[^1]\n", {"1": "2026-07-30_news_yahoo"})
    assert _run(tmp_ticker_dir) == []


def test_a_numeric_citation_missing_from_the_map_is_an_error(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "body")
    _report(tmp_ticker_dir, "Revenue grew.[^1] Margin expanded.[^3]\n",
            {"1": "2026-07-30_news_yahoo"})
    findings = _run(tmp_ticker_dir)
    assert "citation-unresolved" in _codes(findings)
    assert any("3" in f.message for f in findings)


def test_a_map_entry_pointing_at_silver_is_an_error(tmp_ticker_dir: Path):
    write_derived(tmp_ticker_dir, _model_meta("peers_selected", ["peers_candidates"]),
                  {"peers": []}, namespace="peers")
    _report(tmp_ticker_dir, "Its peers.[^1]\n", {"1": "peers_selected"})
    assert "citation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_a_map_entry_pointing_nowhere_is_an_error(tmp_ticker_dir: Path):
    _report(tmp_ticker_dir, "Claim.[^1]\n", {"1": "no_such_source"})
    assert "citation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_a_report_without_a_citation_map_is_an_error(tmp_ticker_dir: Path):
    _report(tmp_ticker_dir, "Claim.[^1]\n", None)
    assert "citation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_a_report_with_no_citations_needs_no_map(tmp_ticker_dir: Path):
    _report(tmp_ticker_dir, "A report with no numeric citations.\n", None)
    assert _run(tmp_ticker_dir) == []


# --- check 5: derivations resolve ----------------------------------------

def test_a_resolvable_derived_from_is_fine(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, _fetch_meta("income_statement_yahoo"), {"rev": 1})
    write_derived(tmp_ticker_dir, _model_meta("peers_ranked", ["income_statement_yahoo"]),
                  {"peers": []}, namespace="peers")
    assert _run(tmp_ticker_dir) == []


def test_an_unresolvable_derived_from_is_an_error(tmp_ticker_dir: Path):
    write_derived(tmp_ticker_dir, _model_meta("peers_ranked", ["no_such_input"]),
                  {"peers": []}, namespace="peers")
    findings = _run(tmp_ticker_dir)
    assert "derivation-unresolved" in _codes(findings)
    assert any("no_such_input" in f.message for f in findings)


def test_a_structured_derived_from_is_checked(tmp_ticker_dir: Path):
    write_structured(tmp_ticker_dir, StructuredMeta(
        id="key_ratios_computed", ticker="PANW", producer="compute",
        title="Key ratios", source="computed", as_of="2026-07-30",
        provider_tool="lib.fetchers.fundamentals",
        fetch_cmd="sra.py prefetch PANW --kinds financials",
        computed_at="2026-07-30T12:00:00+00:00", derived_from=["no_such_input"],
    ), {"pe": 30})
    assert "derivation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_a_wiki_built_from_is_checked(tmp_ticker_dir: Path):
    _write_wiki(tmp_ticker_dir, "competitive", "No claims here.",
                metadata={"section": "competitive", "updated_at": "2026-07-30",
                          "built_from": [{"id": "no_such_input",
                                          "fetched_at": "2026-07-30T12:00:00+00:00"}]})
    assert "derivation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_a_resolvable_wiki_built_from_is_fine(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "body")
    _write_wiki(tmp_ticker_dir, "competitive", "No claims here.",
                metadata={"section": "competitive", "updated_at": "2026-07-30",
                          "built_from": [{"id": "2026-07-30_news_yahoo",
                                          "fetched_at": "2026-07-30T12:00:00+00:00"}]})
    assert _run(tmp_ticker_dir) == []


def test_a_state_derived_stamp_is_checked(tmp_ticker_dir: Path):
    (tmp_ticker_dir / ".state.json").write_text(json.dumps({
        "ticker": "PANW", "data": {}, "wiki": {},
        "derived": {"peers_selected": {
            "current_id": "peers_selected", "updated_at": "2026-07-30T12:00:00+00:00",
            "derived_from": [{"id": "no_such_input",
                              "fetched_at": "2026-07-30T12:00:00+00:00"}],
        }},
        "report": {"last_generated": None, "sections_dirty": []},
    }), encoding="utf-8")
    assert "derivation-unresolved" in _codes(_run(tmp_ticker_dir))


def test_derivation_may_point_at_silver(tmp_ticker_dir: Path):
    """Unlike citation, derivation ACROSS layers is legitimate (§8.1): silver
    is built from silver all the time. Only citation is bronze-only."""
    write_derived(tmp_ticker_dir, _model_meta("peers_candidates", ["x"]),
                  {"c": []}, namespace="peers")
    write_source(tmp_ticker_dir, _source_meta("x"), "body")
    write_derived(tmp_ticker_dir, _model_meta("peers_ranked", ["peers_candidates"]),
                  {"peers": []}, namespace="peers")
    assert _run(tmp_ticker_dir) == []


def test_built_from_may_name_a_macro_artifact(tmp_path: Path):
    """§8.4 check 4 resolves a citation into `_MACRO`, and `built_from` is by
    definition the set of references the page cites — so a shorter reach here
    makes the same id legal in prose and fatal in frontmatter.

    The SPCX valuation page hit exactly that: it cited the FRED risk-free rate
    for its WACC, recorded it in `built_from`, and failed the build for it.
    """
    root = tmp_path / "data"
    ticker = root / "SPCX"
    (ticker / "wiki").mkdir(parents=True)
    (ticker / "sources").mkdir()
    (ticker / "structured").mkdir()
    macro = root / "_MACRO" / "structured"
    macro.mkdir(parents=True)
    (macro / "fred_dgs10.json").write_text(json.dumps({
        "_meta": {"id": "fred_dgs10", "ticker": "_MACRO", "producer": "fetch",
                  "kind": "macro_series", "source": "FRED",
                  "url": "https://fred.stlouisfed.org/series/DGS10",
                  "fetched_at": "2026-08-11T00:00:00+00:00",
                  "as_of": "2026-08-11", "title": "10Y Treasury",
                  "fetch_tool": "lib/fetchers/fred.py",
                  "fetch_cmd": "uv run python sra.py prefetch-macro"},
        "data": {"observations": []},
    }), encoding="utf-8")
    (ticker / "wiki" / "valuation.md").write_text(
        "---\nsection: valuation\nbuilt_from:\n"
        "  - id: fred_dgs10\n    fetched_at: '2026-08-11T00:00:00+00:00'\n"
        "open_questions: []\n---\n\nWACC uses the 10Y.\n", encoding="utf-8")

    codes = {f.code for f in validate(ticker, data_root=root)}
    assert "derivation-unresolved" not in codes


def test_a_critique_is_not_held_to_the_citation_contract(tmp_path: Path):
    """A `<section>.critique.md` is the write wave's working note ABOUT a draft
    (§15.1). It never reaches the report, and its author writes shorthand like
    `[^10q]` when quoting the draft's citations back at it.

    The SPCX build failed its gold gate on exactly that — three shorthand ids in
    one critic's prose — while every real draft was clean. Failing a fatal gate
    on a scratch artifact is how a gate stops being trusted.
    """
    root = tmp_path / "data"
    ticker = root / "SPCX"
    drafts = ticker / "reports" / "2026-08-12" / "sections"
    drafts.mkdir(parents=True)
    (ticker / "wiki").mkdir()
    (ticker / "sources").mkdir()
    (ticker / "structured").mkdir()

    (drafts / "profile.critique.md").write_text(
        "The draft cites [^10q] and [^fool] without resolving them.\n",
        encoding="utf-8")
    codes = {f.code for f in validate(ticker, data_root=root)}
    assert "citation-unresolved" not in codes

    # ...but the draft itself is still held to it.
    (drafts / "profile.md").write_text(
        "## 1. Company Profile\n\nFounded in 2002.[^nope]\n", encoding="utf-8")
    codes = {f.code for f in validate(ticker, data_root=root)}
    assert "citation-unresolved" in codes
