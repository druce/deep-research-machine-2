"""Tests for wiki page primitives and bookkeeping commands (spec §4, §20, §22)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sra
from lib.sections import SECTION_IDS, load_sections
from lib.statefile import load_state
from lib.wiki import (
    append_log, mark_page_dirty, page_path, read_page, update_index, write_page,
)

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

def test_update_index_links_every_page(tmp_ticker_dir: Path):
    """§14.2: the index is the wiki's navigation page. A page name that is not
    a link is a name the reader has to go and find."""
    write_page(tmp_ticker_dir, "competitive", {}, "PANW competes with CRWD.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "](competitive.md)" in text


def test_update_index_prefers_a_declared_summary(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive",
               {"summary": "Share is shifting to CRWD in endpoint."},
               "Scope: Porter's five forces. Persona: strategy consultant.",
               now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "Share is shifting to CRWD in endpoint." in text
    assert "Porter" not in text


def test_update_index_skips_scope_preamble_when_deriving_a_summary(
        tmp_ticker_dir: Path):
    """Every page opens by restating its own assignment. An index of
    assignments is no map at all."""
    write_page(tmp_ticker_dir, "supply_chain", {},
               "**One-line frame.** PANW owns none of its physical supply "
               "chain and buys from a single EMS partner.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "PANW owns none of its physical supply chain" in text
    assert "One-line frame" not in text


def test_update_index_shows_nothing_rather_than_a_fragment(tmp_ticker_dir: Path):
    """A wrong summary is worse than none: it makes the index look maintained
    while misdescribing the page."""
    write_page(tmp_ticker_dir, "competitive", {}, "1.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "| 1. |" not in text


def test_update_index_orders_sections_by_report_order(tmp_ticker_dir: Path):
    for page in ("valuation", "profile", "competitive"):
        write_page(tmp_ticker_dir, page, {}, f"Notes on {page}.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert text.index("profile.md") < text.index("competitive.md") \
        < text.index("valuation.md")


def test_update_index_names_sections_that_were_never_written(
        tmp_ticker_dir: Path):
    """The most important thing the table can say. The old index listed only
    the files it found, so a section nobody researched was invisible."""
    write_page(tmp_ticker_dir, "profile", {}, "Founded in 2005.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "not written" in text
    assert text.count("not written") == len(SECTION_IDS) - 1


def test_update_index_uses_section_titles_when_given_the_config(
        tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "risk_news", {}, "Litigation is pending.", now=NOW)
    text = update_index(tmp_ticker_dir, load_sections()).read_text(encoding="utf-8")
    assert "[Risks](risk_news.md)" in text


def test_update_index_flags_a_dirty_page(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "Notes.", now=NOW)
    mark_page_dirty(tmp_ticker_dir, "competitive")
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "dirty" in text


def test_update_index_rolls_up_open_questions(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "valuation",
               {"open_questions": ["What is the terminal growth rate?"]},
               "Notes.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "## Open questions" in text
    assert "What is the terminal growth rate?" in text


def test_update_index_omits_the_rollup_when_nothing_is_open(
        tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "valuation", {}, "Notes.", now=NOW)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "## Open questions" not in text


def test_update_index_excludes_its_own_bookkeeping(tmp_ticker_dir: Path):
    write_page(tmp_ticker_dir, "competitive", {}, "body", now=NOW)
    append_log(tmp_ticker_dir, "did a thing", now=NOW)
    update_index(tmp_ticker_dir)
    text = update_index(tmp_ticker_dir).read_text(encoding="utf-8")
    assert "00_index.md)" not in text
    assert "](log.md)" in text          # linked as the journal, not listed as a page
    assert "| [log]" not in text


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


def test_append_log_without_cost_flags_is_unchanged(tmp_ticker_dir: Path):
    """Six skills call this. Adding the fields must not rewrite what they
    already emit."""
    append_log(tmp_ticker_dir, "lint: 40 findings", now=NOW)
    text = (tmp_ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")
    assert text == "- 2026-07-30T12:00:00+00:00 lint: 40 findings\n"


def test_append_log_records_what_the_phase_cost(tmp_ticker_dir: Path):
    append_log(tmp_ticker_dir, "lint: 40 findings", now=NOW,
               agents=6, tokens=412_000, minutes=8.4)
    text = (tmp_ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "6 agents · 412k tok · 8.4 min" in text


def test_append_log_links_the_run_log_when_given_a_run(tmp_ticker_dir: Path):
    append_log(tmp_ticker_dir, "write wave", now=NOW, agents=21,
               run="2026-08-11")
    text = (tmp_ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "(../reports/2026-08-11/run_log.md)" in text


def test_append_log_stays_one_entry_per_call(tmp_ticker_dir: Path):
    """§23.4 keeps this a phase journal, not an audit log — the cost line is
    part of the same entry, not a second one."""
    append_log(tmp_ticker_dir, "first", now=NOW, agents=2)
    append_log(tmp_ticker_dir, "second", now=NOW)
    text = (tmp_ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")
    assert text.count("\n- ") == 1      # two entries, one leading "- "
    assert text.startswith("- ")


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
