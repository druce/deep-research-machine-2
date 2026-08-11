"""Tests for wiki page primitives and bookkeeping commands (spec §4, §20, §22)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sra
from lib.statefile import load_state
from lib.wiki import append_log, page_path, read_page, update_index, write_page

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


# --- page IO --------------------------------------------------------------

def test_write_then_read_round_trips(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {"section": "competitive"},
               "PANW competes with CRWD.", now=NOW)
    meta, body = read_page(tmp_ticker_dir, "competitive")
    assert meta["section"] == "competitive"
    assert body.strip() == "PANW competes with CRWD."


def test_write_page_lands_where_the_spec_says(tmp_ticker_dir: Path):
    path = write_page(tmp_ticker_dir, "competitive", {}, "body", now=NOW)
    assert path == page_path(tmp_ticker_dir, "competitive")
    assert path == tmp_ticker_dir / "wiki" / "competitive.md"


def test_write_page_defaults_the_required_frontmatter(tmp_ticker_dir: Path):
    """§20: a page carries section, updated_at, built_from and
    open_questions — consumers read them without a default."""
    write_page(tmp_ticker_dir, "competitive", {}, "body", now=NOW)
    meta, _ = read_page(tmp_ticker_dir, "competitive")
    assert meta["section"] == "competitive"
    assert meta["updated_at"] == NOW.isoformat()
    assert meta["built_from"] == []
    assert meta["open_questions"] == []


def test_write_page_stamps_updated_at_on_every_write(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {"updated_at": "1999-01-01"}, "body", now=NOW)
    meta, _ = read_page(tmp_ticker_dir, "competitive")
    assert meta["updated_at"] == NOW.isoformat()


def test_write_page_preserves_stamped_built_from(tmp_ticker_dir: Path):
    refs = [{"id": "2026-07-30_news_yahoo", "fetched_at": "2026-07-30T12:00:00+00:00"}]
    write_page(tmp_ticker_dir, "competitive", {"built_from": refs}, "body", now=NOW)
    meta, _ = read_page(tmp_ticker_dir, "competitive")
    assert meta["built_from"] == refs


def test_read_page_missing_raises(tmp_ticker_dir: Path):
    with pytest.raises(FileNotFoundError):
        read_page(tmp_ticker_dir, "nope")


def test_write_page_creates_nested_entity_pages(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "entities/crwd", {"section": "competitive"},
               "CrowdStrike.", now=NOW)
    assert (tmp_ticker_dir / "wiki" / "entities" / "crwd.md").is_file()


# --- index ----------------------------------------------------------------

def test_update_index_lists_pages(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "PANW competes with CRWD.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "competitive" in text
    assert "PANW competes with CRWD." in text


def test_update_index_excludes_its_own_bookkeeping(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "body", now=NOW)
    append_log(tmp_ticker_dir, "did a thing", now=NOW)
    update_index(tmp_ticker_dir)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "| 00_index |" not in text
    assert "| log |" not in text


def test_update_index_is_idempotent(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "body", now=NOW)
    first = update_index(tmp_ticker_dir).read_bytes()
    assert update_index(tmp_ticker_dir).read_bytes() == first


def test_update_index_handles_an_empty_wiki(tmp_ticker_dir: Path):
    assert update_index(tmp_ticker_dir).is_file()


def test_update_index_includes_entity_pages(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "entities/crwd", {}, "CrowdStrike.", now=NOW)
    assert "entities/crwd" in update_index(tmp_ticker_dir).read_text(encoding="utf-8")


# --- log ------------------------------------------------------------------

def test_append_log_is_append_only(tmp_ticker_dir: Path):
    append_log(tmp_ticker_dir, "first", now=NOW)
    append_log(tmp_ticker_dir, "second", now=NOW)
    text = (tmp_ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "first" in text and "second" in text
    assert text.index("first") < text.index("second")


def test_append_log_timestamps_each_entry(tmp_ticker_dir: Path):
    append_log(tmp_ticker_dir, "did a thing", now=NOW)
    assert "2026-07-30T12:00:00" in (
        tmp_ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")


def test_append_log_creates_the_file(tmp_ticker_dir: Path):
    (tmp_ticker_dir / "wiki" / "log.md").unlink(missing_ok=True)
    assert append_log(tmp_ticker_dir, "entry", now=NOW).is_file()


# --- CLI bookkeeping ------------------------------------------------------

def test_wiki_log_command_appends(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    assert sra.main(["wiki-log", "PANW", "--entry", "researched the moat",
                     "--data-root", str(tmp_path)]) == 0
    assert "researched the moat" in (
        tmp_path / "PANW" / "wiki" / "log.md").read_text(encoding="utf-8")


def test_wiki_index_command_regenerates(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    write_page(tmp_path / "PANW", "competitive", {}, "PANW competes.", now=NOW)
    assert sra.main(["wiki-index", "PANW", "--data-root", str(tmp_path)]) == 0
    assert "competitive" in (
        tmp_path / "PANW" / "wiki" / "00_index.md").read_text(encoding="utf-8")


def test_mark_dirty_command_records_the_section(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    assert sra.main(["mark-dirty", "PANW", "--section", "competitive",
                     "--data-root", str(tmp_path)]) == 0
    assert load_state(tmp_path / "PANW")["report"]["sections_dirty"] == ["competitive"]


def test_mark_dirty_is_idempotent(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    sra.main(["mark-dirty", "PANW", "--section", "competitive",
              "--data-root", str(tmp_path)])
    sra.main(["mark-dirty", "PANW", "--section", "competitive",
              "--data-root", str(tmp_path)])
    assert load_state(tmp_path / "PANW")["report"]["sections_dirty"] == ["competitive"]


def test_mark_dirty_rejects_an_unknown_section(tmp_path: Path):
    """Sections come from sections.yaml; a typo must not silently create a
    dirty flag nothing will ever consume."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    assert sra.main(["mark-dirty", "PANW", "--section", "not_a_section",
                     "--data-root", str(tmp_path)]) == 1


def test_bookkeeping_commands_take_the_lock(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    (tmp_path / "PANW" / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "prefetch",
        "acquired_at": "2026-08-11T12:00:00+00:00",
    }), encoding="utf-8")
    for argv in (["wiki-log", "PANW", "--entry", "x"],
                 ["wiki-index", "PANW"],
                 ["mark-dirty", "PANW", "--section", "competitive"]):
        assert sra.main(argv + ["--data-root", str(tmp_path)]) == 1


def test_bookkeeping_commands_need_an_initialized_ticker(tmp_path: Path):
    assert sra.main(["wiki-index", "MSFT", "--data-root", str(tmp_path)]) == 1
    assert sra.main(["wiki-log", "MSFT", "--entry", "x",
                     "--data-root", str(tmp_path)]) == 1
    assert sra.main(["mark-dirty", "MSFT", "--section", "competitive",
                     "--data-root", str(tmp_path)]) == 1
