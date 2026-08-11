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
    # The old doc's own fetched_at/as_of (2026-07-01) and the new meta's
    # fetched_at/as_of (2026-08-11, via _meta) are both deliberately different
    # from the injected supersede date (today=2026-08-15). §5 requires the
    # archive stamp to be the supersede date, not either document's own fetch
    # date; with all three dates distinct, a stamp derived from the wrong
    # source produces a filename this test cannot find, so the exact-filename
    # assertion below actually pins the rule instead of passing by coincidence.
    old_meta = prov.SourceMeta(
        id="2026-08-10_news", ticker="PANW", kind="news", source="Yahoo Finance",
        url="https://finance.yahoo.com/quote/PANW/news",
        fetched_at="2026-07-01T09:00:00Z", as_of="2026-07-01",
        title="PANW news roundup", fetch_tool="lib/fetchers/news.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds news",
    )
    p_old = prov.write_source(tmp_ticker_dir, old_meta, "old body")
    old_bytes = p_old.read_bytes()

    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"),
                      "new body", today=date(2026, 8, 15))

    archived = tmp_ticker_dir / "sources" / "archive" / "2026-08-10_news_2026-08-15.md"
    assert archived.exists()
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


# --- Critical 1: archiving must never overwrite an existing archive entry ----

def test_archiving_refuses_to_overwrite_existing_archive_entry(tmp_ticker_dir):
    # The archive is the only copy of superseded evidence. If the computed
    # destination path is already occupied (e.g. a prior partial run already
    # placed something there), the move must refuse rather than silently
    # clobber it via a POSIX rename-over-destination.
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "old body")

    archive_dir = tmp_ticker_dir / "sources" / "archive"
    collision = archive_dir / "2026-08-10_news_2026-08-11.md"
    collision.write_text("pre-existing archived evidence -- must not be lost", encoding="utf-8")

    with pytest.raises(FileExistsError):
        prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"),
                          "new body", today=date(2026, 8, 11))

    # the pre-existing archive entry is untouched
    assert collision.read_text(encoding="utf-8") == "pre-existing archived evidence -- must not be lost"
    # the current file was never moved -- the guard fires before the move,
    # so an interruption here never leaves zero copies of the evidence
    assert (tmp_ticker_dir / "sources" / "2026-08-10_news.md").exists()
    # the attempted new document was never written either
    assert not (tmp_ticker_dir / "sources" / "2026-08-11_news.md").exists()


# --- Critical 2: an archived id must not be reusable ------------------------

def test_write_source_rejects_reusing_an_archived_id(tmp_ticker_dir):
    # Once "2026-08-10_news" is archived, sources/ no longer holds a file at
    # that name -- but §5 requires ids to stay unique across sources/ AND
    # archive/ together, so the id must still be rejected as a target for a
    # brand-new, unrelated document.
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "original body")
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"),
                      "current body", today=date(2026, 8, 11))

    with pytest.raises(FileExistsError):
        prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "a completely different document")

    # the archived original is still reachable at its citation key, unclobbered
    resolved = prov.resolve_source(tmp_ticker_dir, "2026-08-10_news")
    assert resolved is not None
    assert resolved.parent.name == "archive"
    _, body = prov.read_source(resolved)
    assert body == "original body"


# --- Important 2: exact id match, not filename-prefix match, in archive/ ----

def test_resolve_source_exact_match_not_prefix(tmp_ticker_dir):
    # "2026-08-10_news" is a literal string-prefix of "2026-08-10_news_2", and
    # each one's archived filename ("..._<date>.md") is in turn a prefix
    # relationship with the other once the date is appended. A resolver that
    # matched by startswith() instead of exact id equality could return either
    # document for either query; matching by the id recovered via
    # `_archived_id` must not have this ambiguity.
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news"), "first body")
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news_2"), "second body")
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news", supersedes="2026-08-10_news"),
                      "new1", today=date(2026, 8, 12))
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news_2", supersedes="2026-08-10_news_2"),
                      "new2", today=date(2026, 8, 12))

    p1 = prov.resolve_source(tmp_ticker_dir, "2026-08-10_news")
    p2 = prov.resolve_source(tmp_ticker_dir, "2026-08-10_news_2")
    assert p1 is not None and p2 is not None
    assert p1 != p2
    _, body1 = prov.read_source(p1)
    _, body2 = prov.read_source(p2)
    assert body1 == "first body"
    assert body2 == "second body"


def test_resolve_source_does_not_match_a_longer_id_by_prefix(tmp_ticker_dir):
    # A single-candidate variant of the case above that is immune to glob
    # ordering: only the longer id "...news_2" is archived. A resolver
    # matching by startswith() would incorrectly treat that file as a match
    # for the shorter, never-archived id "...news"; exact id matching must
    # return None instead.
    prov.write_source(tmp_ticker_dir, _meta("2026-08-10_news_2"), "only body")
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news_2", supersedes="2026-08-10_news_2"),
                      "new", today=date(2026, 8, 12))
    assert prov.resolve_source(tmp_ticker_dir, "2026-08-10_news") is None


# --- hardening: path containment and atomic write ----------------------------

def test_write_source_rejects_supersedes_path_traversal(tmp_ticker_dir):
    m = _meta("2026-08-11_news", supersedes="../../etc/passwd")
    with pytest.raises(ValueError):
        prov.write_source(tmp_ticker_dir, m, "body")


def test_write_source_rejects_id_path_traversal(tmp_ticker_dir):
    m = _meta("../escape")
    with pytest.raises(ValueError):
        prov.write_source(tmp_ticker_dir, m, "body")


def test_write_source_leaves_no_stray_temp_files(tmp_ticker_dir):
    prov.write_source(tmp_ticker_dir, _meta("2026-08-11_news"), "body")
    names = {p.name for p in (tmp_ticker_dir / "sources").iterdir() if p.is_file()}
    assert names == {"2026-08-11_news.md"}
