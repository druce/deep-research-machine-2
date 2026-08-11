"""Tests for deterministic bronze grep (spec §9, §20)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sra
from lib.grep import Hit, grep
from lib.provenance import SourceMeta, write_source


def _meta(source_id: str, *, kind: str = "news", as_of: str = "2026-07-30",
          title: str = "A headline", url: str = "https://example.com/x",
          supersedes: str | None = None) -> SourceMeta:
    return SourceMeta(
        id=source_id, ticker="PANW", kind=kind, source="yahoo", url=url,
        fetched_at="2026-07-30T12:00:00+00:00", as_of=as_of, title=title,
        fetch_tool="httpx", fetch_cmd="sra.py prefetch PANW --kinds news",
        supersedes=supersedes,
    )


# --- ranking --------------------------------------------------------------

def test_ranks_by_distinct_matched_term_count(tmp_ticker_dir: Path):
    """§9: number of distinct matched terms descending is the first key."""
    write_source(tmp_ticker_dir, _meta("one_term"), "Revenue grew.")
    write_source(tmp_ticker_dir, _meta("two_terms"), "Revenue grew and margin expanded.")
    hits = grep(tmp_ticker_dir, "revenue margin")
    assert [h.source_id for h in hits] == ["two_terms", "one_term"]
    assert hits[0].matched_terms == ["revenue", "margin"]
    assert hits[1].matched_terms == ["revenue"]


def test_equal_term_counts_are_broken_by_as_of_descending(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("older", as_of="2026-07-01"), "Revenue grew.")
    write_source(tmp_ticker_dir, _meta("newer", as_of="2026-08-01"), "Revenue grew.")
    assert [h.source_id for h in grep(tmp_ticker_dir, "revenue")] == ["newer", "older"]


def test_a_full_tie_is_broken_by_source_id(tmp_ticker_dir: Path):
    """§9 calls the ranking deterministic, so a tie on both keys must not fall
    through to filesystem enumeration order."""
    write_source(tmp_ticker_dir, _meta("bbb"), "Revenue grew.")
    write_source(tmp_ticker_dir, _meta("aaa"), "Revenue grew.")
    assert [h.source_id for h in grep(tmp_ticker_dir, "revenue")] == ["aaa", "bbb"]


def test_repeated_matches_of_one_term_do_not_inflate_the_rank(tmp_ticker_dir: Path):
    """The key is DISTINCT terms — otherwise a document that merely repeats a
    word would outrank one that actually covers the whole query."""
    write_source(tmp_ticker_dir, _meta("repetitive"),
                 "Revenue. Revenue. Revenue. Revenue.")
    write_source(tmp_ticker_dir, _meta("covers_both"), "Revenue and margin.")
    assert [h.source_id for h in grep(tmp_ticker_dir, "revenue margin")] == [
        "covers_both", "repetitive"]


def test_top_k_truncates_after_ranking(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("one_term"), "Revenue grew.")
    write_source(tmp_ticker_dir, _meta("two_terms"), "Revenue and margin.")
    hits = grep(tmp_ticker_dir, "revenue margin", top_k=1)
    assert [h.source_id for h in hits] == ["two_terms"]


# --- matching -------------------------------------------------------------

def test_terms_are_case_insensitive(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"), "REVENUE grew.")
    assert len(grep(tmp_ticker_dir, "revenue")) == 1


def test_terms_are_regexes(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"), "FY2026 guidance raised.")
    assert len(grep(tmp_ticker_dir, r"FY20\d\d")) == 1


def test_an_invalid_regex_term_is_rejected(tmp_ticker_dir: Path):
    with pytest.raises(ValueError):
        grep(tmp_ticker_dir, "revenue (unclosed")


def test_no_match_returns_no_hits(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"), "Revenue grew.")
    assert grep(tmp_ticker_dir, "cryptocurrency") == []


def test_frontmatter_is_not_searched(tmp_ticker_dir: Path):
    """Searching metadata would return the provider name or the fetch command
    as if it were evidence."""
    write_source(tmp_ticker_dir, _meta("doc"), "Revenue grew.")
    assert grep(tmp_ticker_dir, "yahoo") == []
    assert grep(tmp_ticker_dir, "prefetch") == []


def test_the_manifest_is_never_searched(tmp_ticker_dir: Path):
    """00_manifest.md lives in sources/ but is a generated catalog; a hit in it
    would hand the researcher a table row instead of evidence."""
    write_source(tmp_ticker_dir, _meta("doc"), "Revenue grew.")
    from lib.manifest import build_manifest
    build_manifest(tmp_ticker_dir)
    assert [h.source_id for h in grep(tmp_ticker_dir, "revenue")] == ["doc"]


def test_one_hit_per_document_with_merged_terms(tmp_ticker_dir: Path):
    """Matches on different lines collapse into a single Hit (§20)."""
    write_source(tmp_ticker_dir, _meta("doc"), "Revenue grew.\n\nMargin expanded.")
    hits = grep(tmp_ticker_dir, "revenue margin")
    assert len(hits) == 1
    assert hits[0].matched_terms == ["revenue", "margin"]


def test_matched_terms_follow_pattern_order(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"), "Margin expanded and revenue grew.")
    assert grep(tmp_ticker_dir, "revenue margin")[0].matched_terms == ["revenue", "margin"]


# --- excerpts -------------------------------------------------------------

def test_excerpt_includes_the_surrounding_context_lines(tmp_ticker_dir: Path):
    body = "line0\nline1\nrevenue grew\nline3\nline4"
    write_source(tmp_ticker_dir, _meta("doc"), body)
    excerpt = grep(tmp_ticker_dir, "revenue", context=1)[0].excerpt
    assert excerpt == "line1\nrevenue grew\nline3"


def test_context_zero_is_the_matching_line_alone(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"), "line0\nrevenue grew\nline2")
    assert grep(tmp_ticker_dir, "revenue", context=0)[0].excerpt == "revenue grew"


def test_excerpt_is_taken_from_the_first_match(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"),
                 "first revenue mention\nfiller\nsecond revenue mention")
    assert grep(tmp_ticker_dir, "revenue", context=0)[0].excerpt == "first revenue mention"


def test_context_is_clamped_at_the_document_edges(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("doc"), "revenue grew\nline1")
    assert grep(tmp_ticker_dir, "revenue", context=5)[0].excerpt == "revenue grew\nline1"


# --- filters --------------------------------------------------------------

def test_kinds_filter_restricts_the_search(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("a_news", kind="news"), "Revenue grew.")
    write_source(tmp_ticker_dir, _meta("a_filing", kind="sec_filing"), "Revenue grew.")
    assert [h.source_id for h in grep(tmp_ticker_dir, "revenue", kinds=["sec_filing"])] == [
        "a_filing"]


def test_archived_sources_are_invisible_by_default(tmp_ticker_dir: Path):
    """§9: grep searches CURRENT sources; the archive is opt-in."""
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    write_source(tmp_ticker_dir,
                 _meta("2026-07-31_news_yahoo", as_of="2026-07-31",
                       supersedes="2026-07-30_news_yahoo"),
                 "Margin expanded.")
    assert [h.source_id for h in grep(tmp_ticker_dir, "revenue")] == []


def test_include_archived_reaches_superseded_evidence(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    write_source(tmp_ticker_dir,
                 _meta("2026-07-31_news_yahoo", as_of="2026-07-31",
                       supersedes="2026-07-30_news_yahoo"),
                 "Margin expanded.")
    hits = grep(tmp_ticker_dir, "revenue", include_archived=True)
    assert [h.source_id for h in hits] == ["2026-07-30_news_yahoo"]


def test_archived_hit_reports_its_own_id_not_the_filename(tmp_ticker_dir: Path):
    """An archived file is named `<id>_<superseded-date>.md`; the hit must
    carry the id a citation would use, not the on-disk stem."""
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    write_source(tmp_ticker_dir,
                 _meta("2026-07-31_news_yahoo", as_of="2026-07-31",
                       supersedes="2026-07-30_news_yahoo"),
                 "Margin expanded.")
    hit = grep(tmp_ticker_dir, "revenue", include_archived=True)[0]
    assert hit.source_id == "2026-07-30_news_yahoo"


# --- hit payload ----------------------------------------------------------

def test_hit_carries_frontmatter_fields(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir,
                 _meta("doc", kind="sec_filing", as_of="2026-05-21",
                       title="Q3 10-Q", url="https://sec.gov/x"),
                 "Revenue grew.")
    hit = grep(tmp_ticker_dir, "revenue")[0]
    assert (hit.kind, hit.as_of, hit.title, hit.url) == (
        "sec_filing", "2026-05-21", "Q3 10-Q", "https://sec.gov/x")
    assert isinstance(hit, Hit)


# --- CLI ------------------------------------------------------------------

def _seed(tmp_path: Path) -> Path:
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    write_source(d, _meta("one_term"), "Revenue grew.")
    write_source(d, _meta("two_terms", as_of="2026-07-29"), "Revenue and margin.")
    return d


def test_grep_command_prints_ranked_json(tmp_path: Path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    assert sra.main(["grep", "PANW", "revenue margin", "--data-root", str(tmp_path)]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert [h["source_id"] for h in hits] == ["two_terms", "one_term"]
    assert hits[0]["matched_terms"] == ["revenue", "margin"]
    assert "excerpt" in hits[0]


def test_grep_command_honors_top_k_and_kinds(tmp_path: Path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    sra.main(["grep", "PANW", "revenue", "--top-k", "1", "--kinds", "news",
              "--data-root", str(tmp_path)])
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_grep_command_kinds_filter_can_exclude_everything(tmp_path: Path, capsys):
    _seed(tmp_path)
    capsys.readouterr()
    sra.main(["grep", "PANW", "revenue", "--kinds", "sec_filing",
              "--data-root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out) == []


def test_grep_command_include_archived_flag(tmp_path: Path, capsys):
    d = _seed(tmp_path)
    write_source(d, _meta("2026-08-02_news_yahoo", as_of="2026-08-02",
                          supersedes="one_term"), "Nothing relevant here.")
    capsys.readouterr()
    sra.main(["grep", "PANW", "revenue", "--data-root", str(tmp_path)])
    without = {h["source_id"] for h in json.loads(capsys.readouterr().out)}
    sra.main(["grep", "PANW", "revenue", "--include-archived",
              "--data-root", str(tmp_path)])
    with_archive = {h["source_id"] for h in json.loads(capsys.readouterr().out)}
    assert "one_term" not in without
    assert "one_term" in with_archive


def test_grep_command_exits_1_when_uninitialized(tmp_path: Path):
    assert sra.main(["grep", "MSFT", "revenue", "--data-root", str(tmp_path)]) == 1


def test_grep_command_exits_1_on_an_invalid_regex(tmp_path: Path):
    _seed(tmp_path)
    assert sra.main(["grep", "PANW", "revenue (unclosed",
                     "--data-root", str(tmp_path)]) == 1


def test_grep_command_rejects_a_traversal_ticker(tmp_path: Path):
    assert sra.main(["grep", "../evil", "revenue", "--data-root", str(tmp_path)]) == 1


def test_a_held_lock_does_not_block_grep(tmp_path: Path):
    """grep is read-only, so it takes no lock (§7.1)."""
    _seed(tmp_path)
    (tmp_path / "PANW" / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "prefetch",
        "acquired_at": "2026-08-11T12:00:00+00:00",
    }), encoding="utf-8")
    assert sra.main(["grep", "PANW", "revenue", "--data-root", str(tmp_path)]) == 0
