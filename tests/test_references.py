"""Citation collection, renumbering, and reference generation (spec §8.2, §15.3, §20).

The rule these tests defend: an assembled report's numbered reference list is
built from the SAME bronze artifacts the section drafts cited by id, in order of
first appearance, and a citation that cannot be resolved is a build defect
rather than a silently-dropped line.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.provenance import SourceMeta, StructuredMeta, write_source, write_structured
from lib.references import (
    build_references_md, collect_citations, renumber, write_citation_map,
)


def _source(ticker_dir: Path, source_id: str, *, kind: str = "sec_filing",
            title: str = "PANW Q3 FY26 10-Q", source: str = "SEC EDGAR",
            url: str = "https://www.sec.gov/x", cited_urls: list[str] | None = None,
            ticker: str = "PANW", body: str = "filing text") -> Path:
    meta = SourceMeta(
        id=source_id, ticker=ticker, kind=kind, source=source, url=url,
        fetched_at="2026-05-21T12:00:00+00:00", as_of="2026-05-20", title=title,
        fetch_tool="httpx", fetch_cmd=f"sra.py prefetch {ticker} --kinds {kind}",
        cited_urls=cited_urls or [],
    )
    return write_source(ticker_dir, meta, body)


def _fetch_structured(ticker_dir: Path, artifact_id: str, *, ticker: str = "PANW",
                      title: str = "Daily prices") -> Path:
    meta = StructuredMeta(
        id=artifact_id, ticker=ticker, producer="fetch", title=title,
        source="yahoo", url="https://finance.yahoo.com/p", as_of="2026-07-30",
        provider_tool="yfinance", fetch_cmd=f"sra.py prefetch {ticker} --kinds prices",
        fetched_at="2026-07-30T12:00:00+00:00",
    )
    return write_structured(ticker_dir, meta, {"close": [1.0]})


def _computed(ticker_dir: Path, artifact_id: str, derived_from: list[str],
              *, title: str = "Technical indicators") -> Path:
    meta = StructuredMeta(
        id=artifact_id, ticker="PANW", producer="compute", title=title,
        source="computed", as_of="2026-07-30", provider_tool="pandas",
        fetch_cmd="sra.py prefetch PANW --kinds technical",
        computed_at="2026-07-30T12:05:00+00:00", derived_from=derived_from,
    )
    return write_structured(ticker_dir, meta, {"rsi": 51.2})


# --- collect_citations ----------------------------------------------------

def test_collect_citations_is_order_of_first_appearance_and_deduped():
    md = (
        "Revenue grew [^2026-05-21_sec_10q].\n"
        "Margins held [^prices_yahoo].\n"
        "As the filing said [^2026-05-21_sec_10q], guidance is intact.\n"
    )
    assert collect_citations(md) == ["2026-05-21_sec_10q", "prices_yahoo"]


def test_collect_citations_empty_when_no_citations():
    assert collect_citations("Plain prose with [a link](https://example.com).") == []


def test_collect_citations_ignores_footnote_definition_lines():
    """A `[^id]:` definition is bookkeeping, not a new claim — it must not
    introduce an id that no claim actually cites."""
    md = "Claim [^a].\n\n[^b]: stray definition\n"
    assert collect_citations(md) == ["a"]


# --- renumber -------------------------------------------------------------

def test_renumber_replaces_ids_with_numbers():
    md = "Revenue [^sec_10q] and price [^prices_yahoo] and again [^sec_10q]."
    out = renumber(md, {"sec_10q": 1, "prices_yahoo": 2})
    assert out == "Revenue [^1] and price [^2] and again [^1]."


def test_renumber_leaves_unmapped_ids_alone():
    md = "Cited [^known] and [^unknown]."
    assert renumber(md, {"known": 1}) == "Cited [^1] and [^unknown]."


def test_renumber_does_not_rewrite_a_prefix_of_a_longer_id():
    md = "[^sec] and [^sec_10q]."
    assert renumber(md, {"sec": 1, "sec_10q": 2}) == "[^1] and [^2]."


# --- build_references_md --------------------------------------------------

def test_source_reference_line_carries_title_source_url_and_fetch_date(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "2026-05-21_sec_10q")
    md = build_references_md(tmp_ticker_dir, ["2026-05-21_sec_10q"])
    assert (
        "[1] PANW Q3 FY26 10-Q — SEC EDGAR, https://www.sec.gov/x — fetched 2026-05-21"
        in md
    )


def test_references_are_numbered_in_the_order_supplied(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "2026-05-21_sec_10q")
    _fetch_structured(tmp_ticker_dir, "prices_yahoo")
    md = build_references_md(tmp_ticker_dir, ["prices_yahoo", "2026-05-21_sec_10q"])
    first, second = [line for line in md.splitlines() if line.startswith("[")][:2]
    assert first.startswith("[1] Daily prices — yahoo,")
    assert second.startswith("[2] PANW Q3 FY26 10-Q — SEC EDGAR,")


def test_aggregator_without_a_url_is_cited_as_itself(tmp_ticker_dir: Path):
    """§8.2: where the aggregator is the claim's only support it is cited as
    such — "Perplexity research, fetched <date>" — with no empty URL field."""
    _source(tmp_ticker_dir, "2026-08-01_perplexity_moat",
            kind="perplexity_research", title="Perplexity research: PANW moat",
            source="Perplexity", url="")
    md = build_references_md(tmp_ticker_dir, ["2026-08-01_perplexity_moat"])
    assert "[1] Perplexity research: PANW moat — Perplexity — fetched 2026-05-21" in md
    assert ", —" not in md


def test_aggregator_lists_its_harvested_origin(tmp_ticker_dir: Path):
    """§5/§8.2: the reference prefers the true origin — when `fetch-urls` has
    harvested a `cited_urls` entry into bronze, that document is named under
    the aggregator entry so the reader lands on the evidence, not the roundup."""
    _source(tmp_ticker_dir, "2026-08-01_news_roundup", kind="news",
            title="Roundup: security spending", source="yahoo",
            url="https://news.example.com/roundup",
            cited_urls=["https://www.sec.gov/x"])
    _source(tmp_ticker_dir, "2026-05-21_sec_10q")
    md = build_references_md(tmp_ticker_dir, ["2026-08-01_news_roundup"])
    assert "[1] Roundup: security spending — yahoo," in md
    assert "origin: PANW Q3 FY26 10-Q — SEC EDGAR, https://www.sec.gov/x" in md


def test_aggregator_with_unharvested_urls_lists_no_origin(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "2026-08-01_news_roundup", kind="news",
            title="Roundup", source="yahoo", url="https://news.example.com/roundup",
            cited_urls=["https://nowhere.example.com/a"])
    md = build_references_md(tmp_ticker_dir, ["2026-08-01_news_roundup"])
    assert "origin:" not in md


def test_computed_citation_expands_to_upstream_bronze(tmp_ticker_dir: Path):
    """§15.3: a computed citation expands to the upstream evidence used in its
    derivation, listed after the computed entry."""
    _fetch_structured(tmp_ticker_dir, "prices_yahoo")
    _computed(tmp_ticker_dir, "technical_computed", ["prices_yahoo"])
    md = build_references_md(tmp_ticker_dir, ["technical_computed"])
    lines = md.splitlines()
    entry = next(i for i, line in enumerate(lines) if line.startswith("[1] Technical indicators"))
    assert "computed" in lines[entry]
    assert "derived from: Daily prices — yahoo," in lines[entry + 1]


def test_computed_expansion_does_not_consume_a_reference_number(tmp_ticker_dir: Path):
    _fetch_structured(tmp_ticker_dir, "prices_yahoo")
    _computed(tmp_ticker_dir, "technical_computed", ["prices_yahoo"])
    _source(tmp_ticker_dir, "2026-05-21_sec_10q")
    md = build_references_md(tmp_ticker_dir, ["technical_computed", "2026-05-21_sec_10q"])
    assert "[2] PANW Q3 FY26 10-Q" in md
    assert "[3]" not in md


def test_computed_expansion_survives_a_derivation_cycle(tmp_ticker_dir: Path):
    """A malformed corpus must not hang assembly; expansion stops at ids it has
    already rendered."""
    _computed(tmp_ticker_dir, "a_computed", ["b_computed"], title="A")
    _computed(tmp_ticker_dir, "b_computed", ["a_computed"], title="B")
    md = build_references_md(tmp_ticker_dir, ["a_computed"])
    assert "derived from: B" in md


def test_archived_source_still_renders_its_reference(tmp_ticker_dir: Path):
    """A refreshed source is archived, not deleted (§5) — a report snapshot that
    cited the superseded version must still produce a reference for it."""
    _source(tmp_ticker_dir, "2026-05-21_sec_10q")
    _source(tmp_ticker_dir, "2026-08-01_sec_10q", title="PANW Q4 FY26 10-Q")
    # Supersede: archive the first, as write_source does on refresh.
    archived = tmp_ticker_dir / "sources" / "archive" / "2026-05-21_sec_10q_2026-08-01.md"
    archived.write_text(
        (tmp_ticker_dir / "sources" / "2026-05-21_sec_10q.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_ticker_dir / "sources" / "2026-05-21_sec_10q.md").unlink()

    md = build_references_md(tmp_ticker_dir, ["2026-05-21_sec_10q"])
    assert "[1] PANW Q3 FY26 10-Q — SEC EDGAR," in md


def test_macro_id_resolves_under_the_macro_tree(tmp_ticker_dir: Path, tmp_macro_dir: Path):
    """§12: shared macro evidence is cited from a ticker's own pages, so its
    reference has to resolve outside the ticker directory."""
    _fetch_structured(tmp_macro_dir, "fred_dgs10", ticker="_MACRO",
                      title="10-Year Treasury constant maturity")
    md = build_references_md(tmp_ticker_dir, ["fred_dgs10"])
    assert "[1] 10-Year Treasury constant maturity — yahoo," in md


def test_unknown_id_raises(tmp_ticker_dir: Path):
    with pytest.raises(ValueError, match="no_such_id"):
        build_references_md(tmp_ticker_dir, ["no_such_id"])


def test_silver_id_raises(tmp_ticker_dir: Path):
    """Silver is never a citation target (§8.1); reference generation refuses it
    rather than printing a derived artifact as if it were evidence."""
    meta = StructuredMeta(
        id="peers_ranked", ticker="PANW", producer="model", title="Ranked peers",
        source="sra-rater", as_of="2026-07-30", generated_at="2026-07-30T12:00:00+00:00",
        derived_from=["peers_candidates"],
    )
    from lib.provenance import write_derived
    write_derived(tmp_ticker_dir, meta, {"peers": []}, namespace="peers")
    with pytest.raises(ValueError, match="silver"):
        build_references_md(tmp_ticker_dir, ["peers_ranked"])


def test_empty_id_list_produces_no_reference_lines(tmp_ticker_dir: Path):
    md = build_references_md(tmp_ticker_dir, [])
    assert "[1]" not in md


# --- write_citation_map ---------------------------------------------------

def test_write_citation_map_writes_string_keyed_json(tmp_path: Path):
    run_dir = tmp_path / "reports" / "2026-08-11"
    run_dir.mkdir(parents=True)
    path = write_citation_map(run_dir, {1: "2026-05-21_sec_10q", 2: "prices_yahoo"})
    assert path == run_dir / "citation_map.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "1": "2026-05-21_sec_10q", "2": "prices_yahoo",
    }
