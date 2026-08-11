import dataclasses
from datetime import date

import pytest

from lib import provenance as prov


# --- kind sets ---------------------------------------------------------

def test_kind_sets_disjoint():
    assert prov.BRONZE_KINDS.isdisjoint(prov.MODEL_KINDS)
    assert "other" in prov.BRONZE_KINDS and "custom" not in prov.BRONZE_KINDS


def test_bronze_kinds_exact_membership():
    # §5's full BRONZE_KINDS list, checked by exact set equality so a dropped
    # (or added) member fails even though it would stay disjoint from MODEL_KINDS.
    assert prov.BRONZE_KINDS == {
        "sec_filing", "wikipedia", "news", "perplexity_research",
        "transcript", "web_page", "press_release", "analyst_note", "other",
    }


def test_model_kinds_exact_membership():
    assert prov.MODEL_KINDS == {"research_answer"}


# --- dataclasses ---------------------------------------------------------

def test_source_meta_required_and_optional_fields():
    names = {f.name for f in dataclasses.fields(prov.SourceMeta)}
    assert names == {
        "id", "ticker", "kind", "source", "url", "fetched_at", "as_of", "title",
        "fetch_tool", "fetch_cmd", "request", "supersedes", "cited_urls",
    }
    required = {
        f.name for f in dataclasses.fields(prov.SourceMeta)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    assert required == {
        "id", "ticker", "kind", "source", "url", "fetched_at", "as_of", "title",
        "fetch_tool", "fetch_cmd",
    }


def test_source_meta_cited_urls_default_not_shared():
    a = prov.SourceMeta(id="a", ticker="PANW", kind="news", source="s", url="u",
                         fetched_at="t", as_of="d", title="t", fetch_tool="ft",
                         fetch_cmd="fc")
    b = prov.SourceMeta(id="b", ticker="PANW", kind="news", source="s", url="u",
                         fetched_at="t", as_of="d", title="t", fetch_tool="ft",
                         fetch_cmd="fc")
    a.cited_urls.append("https://example.com")
    assert b.cited_urls == []
    assert a.cited_urls is not b.cited_urls


def test_structured_meta_required_and_optional_fields():
    names = {f.name for f in dataclasses.fields(prov.StructuredMeta)}
    assert names == {
        "id", "ticker", "producer", "title", "source", "as_of",
        "provider_tool", "fetch_cmd", "url", "request", "fetched_at",
        "computed_at", "generated_at", "period", "currency", "adjusted",
        "derived_from",
    }
    required = {
        f.name for f in dataclasses.fields(prov.StructuredMeta)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    assert required == {"id", "ticker", "producer", "title", "source", "as_of"}


def test_structured_meta_derived_from_default_not_shared():
    a = prov.StructuredMeta(id="a", ticker="PANW", producer="fetch", title="t",
                             source="s", as_of="d")
    b = prov.StructuredMeta(id="b", ticker="PANW", producer="fetch", title="t",
                             source="s", as_of="d")
    a.derived_from.append("2026-08-11_news")
    assert b.derived_from == []
    assert a.derived_from is not b.derived_from


# --- _slug -----------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Zscaler'S SASE Win-Rates!", "zscaler-s-sase-win-rates"),
        # consecutive separators must collapse to one hyphen
        ("A!!!  B", "a-b"),
        # a topic that slugs to nothing (e.g. wholly non-Latin text) must not
        # crash and must produce an empty string, not a stray separator
        ("!!!", ""),
        ("ソニー", ""),
        # a literal hyphen adjacent to characters that get replaced survives
        # the first substitution untouched (hyphen is in the allowed class),
        # so the surrounding replaced-chars-turned-hyphens form a run next to
        # it (e.g. " -- " -> "----") that only the separate -{2,} collapse
        # step catches; the other four cases above never produce such a run,
        # since none of them puts a literal "-" next to disallowed chars, so
        # this case is required to keep that collapse step covered
        ("SASE -- Win Rates", "sase-win-rates"),
    ],
)
def test_slug_table(text, expected):
    assert prov._slug(text) == expected


# --- _archived_id ------------------------------------------------------

def test_archived_id_strips_trailing_date():
    assert prov._archived_id("2026-08-11_news_2026-08-12.md") == "2026-08-11_news"


def test_archived_id_handles_id_ending_in_numeric_suffix():
    # the id itself ends in "_2"; only the trailing date-shaped suffix is stripped
    assert prov._archived_id("2026-08-11_news_2_2026-08-12.md") == "2026-08-11_news_2"


def test_archived_id_total_over_non_conforming_filename():
    # a filename with no trailing _<date> suffix must still lose its extension,
    # not be returned verbatim with ".md" attached (that failure mode drops the
    # file out of the `taken` set and reuses its citation key, §5)
    assert prov._archived_id("2026-08-11_news.md") == "2026-08-11_news"


# --- make_source_id ------------------------------------------------------

def test_make_source_id_basic(tmp_ticker_dir):
    sid = prov.make_source_id("news", date(2026, 8, 11), ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_news"


def test_make_source_id_topic_slugged(tmp_ticker_dir):
    sid = prov.make_source_id("web_page", date(2026, 8, 11),
                              topic="Zscaler'S SASE Win-Rates!", ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_web_page_zscaler-s-sase-win-rates"


def test_make_source_id_counts_archive(tmp_ticker_dir):
    # §5: ids unique across sources/ AND sources/archive/
    (tmp_ticker_dir / "sources" / "archive" / "2026-08-11_news_2026-08-12.md").write_text("x")
    (tmp_ticker_dir / "sources" / "2026-08-11_news_2.md").write_text("x")
    sid = prov.make_source_id("news", date(2026, 8, 11), ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_news_3"


def test_make_source_id_counts_non_conforming_archive_filename(tmp_ticker_dir):
    # a hand-placed/migrated archive file with no trailing _<date> suffix must
    # still be counted as taken, not silently dropped (Finding 2)
    (tmp_ticker_dir / "sources" / "archive" / "2026-08-11_news.md").write_text("x")
    sid = prov.make_source_id("news", date(2026, 8, 11), ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_news_2"


def test_make_source_id_empty_slug_topic_produces_conforming_id(tmp_ticker_dir):
    # a topic that slugs to empty (e.g. wholly non-Latin text) must not leave a
    # stray trailing underscore in the id (Finding 1)
    sid = prov.make_source_id("web_page", date(2026, 8, 11), topic="!!!",
                              ticker_dir=tmp_ticker_dir)
    assert sid == "2026-08-11_web_page"

    # once that id is taken, a second empty-slug topic on the same day must
    # collide cleanly onto the ordinary numeric-suffix path, not produce a
    # second malformed id like "..._web_page__2"
    (tmp_ticker_dir / "sources" / f"{sid}.md").write_text("x")
    sid2 = prov.make_source_id("web_page", date(2026, 8, 11), topic="???",
                               ticker_dir=tmp_ticker_dir)
    assert sid2 == "2026-08-11_web_page_2"


def test_make_source_id_missing_sources_dir_raises(tmp_path):
    missing = tmp_path / "data" / "NOPE"
    with pytest.raises(FileNotFoundError):
        prov.make_source_id("news", date(2026, 8, 11), ticker_dir=missing)


# --- write_source / read_source frontmatter round-trip -----------------------

def _full_meta(sid):
    return prov.SourceMeta(
        id=sid, ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/Archives/example",
        fetched_at="2026-05-21T14:22:03Z", as_of="2026-04-30",
        title="PANW Q3 FY26 10-Q", fetch_tool="lib/fetchers/edgar.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds filings",
        request={"method": "GET", "params": {"symbol": "PANW"}},
        supersedes="2026-02-20_sec_10q",
        cited_urls=["https://ir.zscaler.com/example"],
    )


def test_read_source_round_trips_all_fields(tmp_ticker_dir):
    # supersedes target does not need to exist on disk for this round-trip test;
    # write_source's archiving step is a no-op when the named source is absent
    meta = _full_meta("2026-05-21_sec_10q")
    path = prov.write_source(tmp_ticker_dir, meta, "<body copied from the source>")
    read_meta, body = prov.read_source(path)
    assert read_meta == meta
    assert body == "<body copied from the source>"


def test_write_source_omits_empty_optional_keys_from_frontmatter(tmp_ticker_dir):
    # §5's example shows request/supersedes/cited_urls only when present; a bare
    # source (none of the three) must not emit null or empty-list placeholders
    meta = prov.SourceMeta(
        id="2026-08-11_news", ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/news",
        fetched_at="2026-08-11T12:00:00Z", as_of="2026-08-11",
        title="PANW news roundup", fetch_tool="lib/fetchers/news.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds news",
    )
    path = prov.write_source(tmp_ticker_dir, meta, "body text")
    text = path.read_text(encoding="utf-8")
    assert "request" not in text
    assert "supersedes" not in text
    assert "cited_urls" not in text


def test_read_source_fills_defaults_for_omitted_optional_keys(tmp_ticker_dir):
    meta = prov.SourceMeta(
        id="2026-08-11_news", ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/news",
        fetched_at="2026-08-11T12:00:00Z", as_of="2026-08-11",
        title="PANW news roundup", fetch_tool="lib/fetchers/news.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds news",
    )
    path = prov.write_source(tmp_ticker_dir, meta, "body text")
    read_meta, _ = prov.read_source(path)
    assert read_meta.request is None
    assert read_meta.supersedes is None
    assert read_meta.cited_urls == []


# --- resolve_source ------------------------------------------------------

def test_resolve_source_returns_none_when_neither_exists(tmp_ticker_dir):
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-11_news") is None
