"""Tests for the ADVISORY wiki lint (spec §22.1).

Advisory is the whole point: these findings are style and hygiene signals for a
human or a follow-up model pass, so `wiki-lint` always exits 0. Mixing them
into `validate` would either block builds on prose or train people to ignore a
fatal gate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sra
from lib.provenance import SourceMeta, write_source
from lib.sections import load_sections
from lib.wiki import update_index, wiki_lint, write_page

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _cfg() -> dict:
    return load_sections()


def _lint(ticker_dir: Path) -> list:
    return wiki_lint(ticker_dir, _cfg())


def _codes(findings: list) -> set[str]:
    return {f.code for f in findings}


def _bronze(ticker_dir: Path, source_id: str = "2026-07-30_news_yahoo") -> None:
    write_source(ticker_dir, SourceMeta(
        id=source_id, ticker="PANW", kind="news", source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of="2026-07-30", title="A headline", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news",
    ), "Revenue grew.")


# --- clean baseline -------------------------------------------------------

def test_a_clean_page_produces_no_findings(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "competitive",
               {"summary": "PANW holds share against CRWD and FTNT.",
                "built_from": [{"id": "2026-07-30_news_yahoo",
                                "fetched_at": "2026-07-30T12:00:00+00:00"}]},
               "PANW holds a strong position.[^2026-07-30_news_yahoo]", now=NOW)
    update_index(tmp_ticker_dir)
    assert _lint(tmp_ticker_dir) == []


def test_a_page_without_a_declared_summary_is_flagged(tmp_ticker_dir: Path):
    """§14.2 asks the synthesizer for the one-line description. Without it the
    index derives one from prose that opens with scope and period conventions,
    so the row describes the assignment rather than the finding."""
    write_page(tmp_ticker_dir, "competitive", {}, "PANW competes.", now=NOW)
    update_index(tmp_ticker_dir)
    assert "missing-summary" in _codes(_lint(tmp_ticker_dir))


def test_an_empty_wiki_produces_no_findings(tmp_ticker_dir: Path):
    assert _lint(tmp_ticker_dir) == []


def test_every_finding_is_advisory(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "Revenue rose 15.4% last year.",
               now=NOW)
    findings = _lint(tmp_ticker_dir)
    assert findings
    assert all(f.severity == "warning" for f in findings)


# --- numeric claim without citation --------------------------------------

def test_a_numeric_claim_without_a_citation_is_flagged(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "Revenue rose 15.4% last year.",
               now=NOW)
    assert "uncited-number" in _codes(_lint(tmp_ticker_dir))


def test_a_cited_numeric_claim_is_not_flagged(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "competitive", {},
               "Revenue rose 15.4% last year.[^2026-07-30_news_yahoo]", now=NOW)
    assert "uncited-number" not in _codes(_lint(tmp_ticker_dir))


def test_the_citation_must_be_in_the_same_paragraph(tmp_ticker_dir: Path):
    """§22.1 scans per paragraph: a citation three paragraphs away does not
    support this number, and treating the page as one unit would let a single
    citation launder every figure on it."""
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "competitive", {},
               "Revenue rose 15.4% last year.\n\nSomething else.[^2026-07-30_news_yahoo]",
               now=NOW)
    assert "uncited-number" in _codes(_lint(tmp_ticker_dir))


def test_a_bare_year_is_not_a_numeric_claim(tmp_ticker_dir: Path):
    """Otherwise every sentence mentioning a date would demand a citation and
    the check would be noise."""
    write_page(tmp_ticker_dir, "competitive", {},
               "The company was founded in 2005 and listed later.", now=NOW)
    assert "uncited-number" not in _codes(_lint(tmp_ticker_dir))


def test_headings_are_not_scanned_for_numeric_claims(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "## Segment mix 15.4%", now=NOW)
    assert "uncited-number" not in _codes(_lint(tmp_ticker_dir))


# --- forward-looking number without a status tag -------------------------

def test_a_forward_looking_number_without_a_status_tag_is_flagged(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "valuation", {},
               "FY27 revenue grows 20%.[^2026-07-30_news_yahoo]", now=NOW)
    assert "untagged-forward-number" in _codes(_lint(tmp_ticker_dir))


def test_a_tagged_forward_looking_number_is_fine(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "valuation", {},
               "[GUIDANCE] FY27 revenue grows 20%.[^2026-07-30_news_yahoo]", now=NOW)
    assert "untagged-forward-number" not in _codes(_lint(tmp_ticker_dir))


def test_a_historical_number_needs_no_status_tag(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "financial", {},
               "Revenue rose 15.4% last year.[^2026-07-30_news_yahoo]", now=NOW)
    assert "untagged-forward-number" not in _codes(_lint(tmp_ticker_dir))


# --- section ownership ----------------------------------------------------

def test_a_section_ownership_breach_is_flagged(tmp_ticker_dir: Path):
    """§18's ownership contract: the seven sections are written in parallel by
    agents that cannot see each other's drafts, so "avoid repetition" only
    works as a machine-checkable contract."""
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "profile", {},
               "Its market share is unmatched.[^2026-07-30_news_yahoo]", now=NOW)
    assert "section-ownership" in _codes(_lint(tmp_ticker_dir))


def test_the_owning_section_may_use_its_own_facts(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "competitive", {},
               "Its market share is unmatched.[^2026-07-30_news_yahoo]", now=NOW)
    assert "section-ownership" not in _codes(_lint(tmp_ticker_dir))


# --- duplicate figures ----------------------------------------------------

def test_the_same_figure_on_two_pages_is_flagged(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    for page in ("competitive", "financial"):
        write_page(tmp_ticker_dir, page, {},
                   f"Revenue rose 15.4% in {page}.[^2026-07-30_news_yahoo]", now=NOW)
    assert "duplicate-figure" in _codes(_lint(tmp_ticker_dir))


def test_a_figure_used_once_is_not_flagged(tmp_ticker_dir: Path):
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "competitive", {},
               "Revenue rose 15.4%.[^2026-07-30_news_yahoo]", now=NOW)
    write_page(tmp_ticker_dir, "financial", {},
               "Margin reached 78.2%.[^2026-07-30_news_yahoo]", now=NOW)
    assert "duplicate-figure" not in _codes(_lint(tmp_ticker_dir))


def test_repeating_a_figure_on_one_page_is_not_a_duplicate(tmp_ticker_dir: Path):
    """The check is about the same fact being restated in two SECTIONS (§18),
    not about a page referring to its own number twice."""
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "financial", {},
               "Revenue rose 15.4%.[^2026-07-30_news_yahoo]\n\n"
               "That 15.4% is the fastest in years.[^2026-07-30_news_yahoo]", now=NOW)
    assert "duplicate-figure" not in _codes(_lint(tmp_ticker_dir))


# --- built_from and index -------------------------------------------------

def test_an_invalid_built_from_is_flagged(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive",
               {"built_from": [{"id": "no_such_input",
                                "fetched_at": "2026-07-30T12:00:00+00:00"}]},
               "No claims here.", now=NOW)
    assert "invalid-built-from" in _codes(_lint(tmp_ticker_dir))


def test_an_entity_page_missing_from_the_index_is_flagged(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "entities/crwd", {}, "CrowdStrike.", now=NOW)
    # deliberately not regenerating the index
    (tmp_ticker_dir / "wiki" / "00_index.md").write_text("# Wiki index\n", encoding="utf-8")
    assert "page-not-indexed" in _codes(_lint(tmp_ticker_dir))


def test_an_indexed_entity_page_is_fine(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "entities/crwd", {}, "CrowdStrike.", now=NOW)
    update_index(tmp_ticker_dir)
    assert "entity-not-indexed" not in _codes(_lint(tmp_ticker_dir))


# --- CLI ------------------------------------------------------------------

def test_wiki_lint_exits_0_even_with_findings(tmp_path: Path, capsys):
    """§22.1 is advisory. An advisory check that fails the build is a fatal
    check nobody agreed to."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    write_page(tmp_path / "PANW", "competitive", {},
               "Revenue rose 15.4% last year.", now=NOW)
    capsys.readouterr()
    assert sra.main(["wiki-lint", "PANW", "--data-root", str(tmp_path)]) == 0
    findings = json.loads(capsys.readouterr().out)
    assert findings
    assert all(f["severity"] == "warning" for f in findings)


def test_wiki_lint_exits_0_on_a_clean_wiki(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    capsys.readouterr()
    assert sra.main(["wiki-lint", "PANW", "--data-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_wiki_lint_needs_an_initialized_ticker(tmp_path: Path):
    assert sra.main(["wiki-lint", "MSFT", "--data-root", str(tmp_path)]) == 1


def test_a_held_lock_does_not_block_wiki_lint(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    (tmp_path / "PANW" / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "prefetch",
        "acquired_at": "2026-08-11T12:00:00+00:00",
    }), encoding="utf-8")
    assert sra.main(["wiki-lint", "PANW", "--data-root", str(tmp_path)]) == 0


def test_a_status_tag_may_carry_its_provider_and_as_of_date(tmp_ticker_dir: Path):
    """§18's own example is `[CONSENSUS, yfinance, as of 2026-07-30]`. A check
    that demanded the bare tag fired on prose written exactly as the spec
    instructs."""
    _bronze(tmp_ticker_dir)
    write_page(tmp_ticker_dir, "valuation",
               {"summary": "Consensus embeds a margin the data does not support.",
                "built_from": [{"id": "2026-07-30_news_yahoo",
                                "fetched_at": "2026-07-30T12:00:00+00:00"}]},
               "FY2027 revenue of $9.8B [CONSENSUS, yfinance, as of 2026-07-30] "
               "against guidance.[^2026-07-30_news_yahoo]", now=NOW)
    update_index(tmp_ticker_dir)
    assert "untagged-forward-number" not in _codes(_lint(tmp_ticker_dir))
