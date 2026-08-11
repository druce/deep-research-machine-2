"""Tests for sources/00_manifest.md, the researcher's entry point (spec §5.1, §9)."""
from __future__ import annotations

import json
from pathlib import Path

import sra
from lib.manifest import MANIFEST_NAME, build_manifest, manifest_rows
from lib.provenance import SourceMeta, write_source


def _meta(source_id: str, *, kind: str = "news", as_of: str = "2026-07-30",
          title: str = "A headline", supersedes: str | None = None) -> SourceMeta:
    return SourceMeta(
        id=source_id, ticker="PANW", kind=kind, source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of=as_of, title=title, fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news", supersedes=supersedes,
    )


def _rows_by_id(ticker_dir: Path) -> dict[str, dict]:
    return {r["id"]: r for r in manifest_rows(ticker_dir)}


# --- which sources appear -------------------------------------------------

def test_lists_a_current_source(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    rows = manifest_rows(tmp_ticker_dir)
    assert [r["id"] for r in rows] == ["2026-07-30_news_yahoo"]


def test_superseded_source_is_replaced_by_its_successor(tmp_ticker_dir: Path):
    """§5.1: the manifest is a catalog of what is true NOW. Listing every
    superseded copy would grow the researcher's entry point without adding
    evidence."""
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Old text.")
    write_source(
        tmp_ticker_dir,
        _meta("2026-07-31_news_yahoo", as_of="2026-07-31",
              supersedes="2026-07-30_news_yahoo"),
        "New text.",
    )
    assert [r["id"] for r in manifest_rows(tmp_ticker_dir)] == ["2026-07-31_news_yahoo"]


def test_manifest_never_lists_itself(tmp_ticker_dir: Path):
    """00_manifest.md lives in sources/ but is generated, not evidence."""
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    build_manifest(tmp_ticker_dir)
    build_manifest(tmp_ticker_dir)
    assert [r["id"] for r in manifest_rows(tmp_ticker_dir)] == ["2026-07-30_news_yahoo"]


def test_empty_sources_dir_still_produces_a_table(tmp_ticker_dir: Path):
    path = build_manifest(tmp_ticker_dir)
    text = path.read_text(encoding="utf-8")
    assert manifest_rows(tmp_ticker_dir) == []
    assert "| id | kind | as_of | bytes | one-line contents |" in text


# --- row content ----------------------------------------------------------

def test_row_carries_kind_and_as_of_from_frontmatter(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir,
                 _meta("2026-05-21_sec_filing", kind="sec_filing", as_of="2026-05-21"),
                 "Item 1A. Risk Factors")
    row = _rows_by_id(tmp_ticker_dir)["2026-05-21_sec_filing"]
    assert row["kind"] == "sec_filing"
    assert row["as_of"] == "2026-05-21"


def test_bytes_column_matches_file_size(tmp_ticker_dir: Path):
    path = write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    assert _rows_by_id(tmp_ticker_dir)["2026-07-30_news_yahoo"]["bytes"] == \
        path.stat().st_size


def test_summary_prefers_the_frontmatter_title(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo", title="Q3 beat"),
                 "Some body text that is not the title.")
    assert _rows_by_id(tmp_ticker_dir)["2026-07-30_news_yahoo"]["summary"] == "Q3 beat"


def test_summary_falls_back_to_first_non_empty_body_line(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo", title=""),
                 "\n\n   \nFirst real line.\nSecond line.")
    assert _rows_by_id(tmp_ticker_dir)["2026-07-30_news_yahoo"]["summary"] == \
        "First real line."


def test_summary_is_truncated_to_100_chars(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo", title="x" * 300), "body")
    assert len(_rows_by_id(tmp_ticker_dir)["2026-07-30_news_yahoo"]["summary"]) == 100


def test_summary_pipes_are_escaped_in_the_table(tmp_ticker_dir: Path):
    """An unescaped pipe in a title would split the row into phantom columns
    and corrupt the one file every researcher reads first."""
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo", title="A | B"), "body")
    line = next(ln for ln in build_manifest(tmp_ticker_dir)
                .read_text(encoding="utf-8").splitlines()
                if "2026-07-30_news_yahoo" in ln)
    assert r"\|" in line
    # 5 columns -> 6 delimiters; the title must contribute none of them
    assert line.replace(r"\|", "").count("|") == 6


def test_summary_is_a_single_line(tmp_ticker_dir: Path):
    """A newline inside the summary would break the row in half."""
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo", title="one\ntwo"), "body")
    assert "\n" not in _rows_by_id(tmp_ticker_dir)["2026-07-30_news_yahoo"]["summary"]


# --- ordering and idempotence --------------------------------------------

def test_rows_sorted_by_as_of_desc_then_id(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_b", as_of="2026-07-30"), "b")
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_a", as_of="2026-07-30"), "a")
    write_source(tmp_ticker_dir, _meta("2026-08-01_news_c", as_of="2026-08-01"), "c")
    assert [r["id"] for r in manifest_rows(tmp_ticker_dir)] == [
        "2026-08-01_news_c", "2026-07-30_news_a", "2026-07-30_news_b"]


def test_regenerating_is_byte_identical(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    first = build_manifest(tmp_ticker_dir).read_bytes()
    assert build_manifest(tmp_ticker_dir).read_bytes() == first


def test_build_manifest_writes_where_the_spec_says(tmp_ticker_dir: Path):
    path = build_manifest(tmp_ticker_dir)
    assert path == tmp_ticker_dir / "sources" / MANIFEST_NAME
    assert path.is_file()


def test_rebuild_reflects_a_removed_source(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _meta("2026-07-30_news_yahoo"), "Revenue grew.")
    build_manifest(tmp_ticker_dir)
    (tmp_ticker_dir / "sources" / "2026-07-30_news_yahoo.md").unlink()
    assert manifest_rows(tmp_ticker_dir) == []
    assert "2026-07-30_news_yahoo" not in \
        build_manifest(tmp_ticker_dir).read_text(encoding="utf-8")


# --- CLI ------------------------------------------------------------------

def test_manifest_command_prints_the_path(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    capsys.readouterr()
    assert sra.main(["manifest", "PANW", "--data-root", str(tmp_path)]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == str(tmp_path / "PANW" / "sources" / MANIFEST_NAME)
    assert Path(printed).is_file()


def test_manifest_command_exits_1_when_uninitialized(tmp_path: Path):
    assert sra.main(["manifest", "MSFT", "--data-root", str(tmp_path)]) == 1


def test_manifest_command_rejects_a_traversal_ticker(tmp_path: Path):
    assert sra.main(["manifest", "../evil", "--data-root", str(tmp_path)]) == 1


def test_manifest_command_takes_the_lock(tmp_path: Path):
    """`manifest` writes into sources/, so it is a mutating command (§7.1)."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    (tmp_path / "PANW" / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "prefetch",
        "acquired_at": "2026-07-30T12:00:00+00:00",
    }), encoding="utf-8")
    assert sra.main(["manifest", "PANW", "--data-root", str(tmp_path)]) == 1
