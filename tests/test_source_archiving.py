"""§24 "Source archiving" acceptance tests for write_source / resolve_source / read_source.

See sra6-spec.md §5 (Immutability and archiving, supersedes) and §20 (Source I/O contracts).
"""

from __future__ import annotations

from datetime import date

import pytest

from lib import provenance as prov


def _meta(sid, supersedes=None):
    return prov.SourceMeta(
        id=sid, ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/news",
        fetched_at="2026-08-11T12:00:00Z", as_of="2026-08-11",
        title="PANW news roundup", fetch_tool="lib/fetchers/news.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds news",
        supersedes=supersedes)


def test_write_rejects_model_kind(tmp_ticker_dir):
    m = _meta("2026-08-11_research_answer"); m.kind = "research_answer"
    with pytest.raises(ValueError):
        prov.write_source(tmp_ticker_dir, m, "body")


def test_overwrite_raises(tmp_ticker_dir):
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news"), "v1")
    with pytest.raises(FileExistsError):
        prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news"), "v2")


def test_supersede_archives_byte_identical(tmp_ticker_dir):
    p_old = prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "old body")
    old_bytes = p_old.read_bytes()
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"),
                      "new body", today=date(2026, 8, 11))
    archived = tmp_ticker_dir / "sources" / "archive" / "2026-08-10_news_2026-08-11.md"
    assert archived.read_bytes() == old_bytes          # move, not edit (§5)
    assert not p_old.exists()


def test_resolve_current_then_archive(tmp_ticker_dir):
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "old")
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"),
                      "new", today=date(2026, 8, 12))
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-11_news").parent.name == "sources"
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-10_news").parent.name == "archive"
    assert prov.resolve_source(tmp_ticker_dir, "nope") is None


def test_archiving_idempotent_rerun(tmp_ticker_dir):
    # §24: re-running the same refresh (old file already archived) must not crash
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "old")
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"), "new")
    archived_path = tmp_ticker_dir / "sources" / "archive" / f"2026-08-10_news_{date.today().isoformat()}.md"
    archived_bytes_before = archived_path.read_bytes()

    m2 = _meta("2026-08-11_news_2", supersedes="2026-08-10_news")  # retry with fresh id
    p2 = prov.write_source(tmp_ticker_dir, m2, "new again")        # supersedes target already gone: no-op

    # second write succeeded and landed in sources/
    assert p2.exists()
    assert p2.parent.name == "sources"

    # the previously archived file is untouched
    assert archived_path.read_bytes() == archived_bytes_before

    # no stray file appears in sources/ or sources/archive/
    assert sorted(p.name for p in (tmp_ticker_dir / "sources").glob("*.md")) == [
        "2026-08-11_news.md", "2026-08-11_news_2.md",
    ]
    assert sorted(p.name for p in (tmp_ticker_dir / "sources" / "archive").glob("*.md")) == [
        archived_path.name,
    ]

    # both ids still resolve
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-11_news").parent.name == "sources"
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-11_news_2").parent.name == "sources"
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-10_news").parent.name == "archive"
