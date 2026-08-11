"""Tests for .state.json freshness stamps and build state (spec §7, §10.1)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from lib.statefile import (
    EVENT_POLICY_FALLBACK_DAYS,
    init_state,
    load_state,
    mark_section_dirty,
    record_derived,
    record_fetch,
    save_state,
    stale_kinds,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


# --- shape and round-trip -------------------------------------------------

def test_init_and_roundtrip(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    assert state["ticker"] == "PANW"
    save_state(tmp_ticker_dir, state)
    assert load_state(tmp_ticker_dir)["ticker"] == "PANW"
    with pytest.raises(FileExistsError):
        init_state(tmp_ticker_dir, "PANW")


def test_init_state_has_every_spec_block(tmp_ticker_dir: Path):
    """§7's example state carries `derived`, `peers_asked_at` and
    `report.last_generated` alongside the older blocks; consumers read them
    without a `.get` default, so `init_state` must create them."""
    state = init_state(tmp_ticker_dir, "PANW")
    assert state["data"] == {}
    assert state["derived"] == {}
    assert state["wiki"] == {}
    assert state["peers_asked_at"] is None
    assert state["report"] == {"last_generated": None, "sections_dirty": []}


def test_save_state_leaves_no_tmp_file(tmp_ticker_dir: Path):
    """Atomic write: tmp + os.replace, with nothing left behind (§7.1)."""
    state = init_state(tmp_ticker_dir, "PANW")
    save_state(tmp_ticker_dir, state)
    leftovers = [p.name for p in tmp_ticker_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert json.loads((tmp_ticker_dir / ".state.json").read_text(encoding="utf-8"))


# --- record_fetch ---------------------------------------------------------

def test_record_fetch_normalizes_to_list(tmp_ticker_dir: Path):
    """§7: `current_ids` is ALWAYS stored as a list, whether the caller passed
    one id or many."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "prices", "prices_yahoo", NOW, {"policy_days": 1})
    record_fetch(state, "financials", ["income_statement_yahoo", "key_ratios_computed"],
                 NOW, {"policy": "on_earnings"})
    assert state["data"]["prices"]["current_ids"] == ["prices_yahoo"]
    assert state["data"]["financials"]["current_ids"] == [
        "income_statement_yahoo", "key_ratios_computed"]
    assert "current_id" not in state["data"]["prices"]


def test_record_fetch_stores_policy_and_isoformat_stamp(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "news", "2026-07-30_news_yahoo", NOW, {"policy_days": 5})
    entry = state["data"]["news"]
    assert entry["fetched_at"] == NOW.isoformat()
    assert entry["policy_days"] == 5


def test_record_fetch_copies_the_id_list(tmp_ticker_dir: Path):
    """State must not alias a caller's list — a fetcher that goes on mutating
    its own list would otherwise silently rewrite committed state."""
    state = init_state(tmp_ticker_dir, "PANW")
    ids = ["income_statement_yahoo"]
    record_fetch(state, "financials", ids, NOW, {"policy": "on_earnings"})
    ids.append("mutated_after_the_fact")
    assert state["data"]["financials"]["current_ids"] == ["income_statement_yahoo"]


# --- time and event policies ----------------------------------------------

def test_policy_days_staleness(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "prices", "prices_yahoo", NOW - timedelta(days=2), {"policy_days": 1})
    record_fetch(state, "news", "2026-07-30_news_yahoo", NOW - timedelta(hours=3),
                 {"policy_days": 5})
    assert stale_kinds(state, NOW) == ["prices"]


def test_on_earnings_policy_uses_last_past_event(tmp_ticker_dir: Path):
    """§7: the signal is the most recent PAST earnings date, not the next
    scheduled one. A forward-looking date only fires in the window before it
    arrives; once it passes and the calendar refetches to a new future
    estimate the check goes permanently silent."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "financials", "balance_sheet_yahoo", NOW - timedelta(days=10),
                 {"policy": "on_earnings"})
    # earnings landed after the last fetch, on or before now -> stale
    assert stale_kinds(state, NOW, last_earnings=date(2026, 7, 25)) == ["financials"]
    # earnings predate the fetch -> the fetch already saw them -> fresh
    assert stale_kinds(state, NOW, last_earnings=date(2026, 7, 15)) == []
    # no calendar signal at all -> 7-day fallback, and 10 days is past it
    assert stale_kinds(state, NOW) == ["financials"]


def test_on_new_filing_ignores_earnings_and_uses_the_fallback(tmp_ticker_dir: Path):
    """§7: `on_new_filing` has no filing-date signal yet (§28.4), so it always
    falls back to EVENT_POLICY_FALLBACK_DAYS rather than consuming an earnings
    date it has nothing to do with."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "filings", "sec_financials_edgar", NOW - timedelta(days=10),
                 {"policy": "on_new_filing"})
    assert stale_kinds(state, NOW, last_earnings=date(2026, 7, 15)) == ["filings"]
    assert stale_kinds(state, NOW, last_earnings=date(2026, 7, 25)) == ["filings"]
    state["data"].clear()
    record_fetch(state, "filings", "sec_financials_edgar", NOW - timedelta(days=2),
                 {"policy": "on_new_filing"})
    assert stale_kinds(state, NOW, last_earnings=date(2026, 7, 25)) == []


def test_event_policy_fallback_days_is_seven():
    assert EVENT_POLICY_FALLBACK_DAYS == 7


def test_naive_now_is_read_as_utc(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "prices", "prices_yahoo", NOW - timedelta(days=2), {"policy_days": 1})
    assert stale_kinds(state, NOW.replace(tzinfo=None)) == ["prices"]


# --- missing-artifact staleness (§10.1) -----------------------------------

def test_stale_when_artifact_missing(tmp_ticker_dir: Path):
    """§10.1: a kind is stale when any id in `current_ids` is missing from
    disk, even though its time policy has not expired."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "prices", "prices_yahoo", NOW, {"policy_days": 1})
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == ["prices"]
    # ...and without a ticker_dir the on-disk check does not run at all
    assert stale_kinds(state, NOW) == []


def test_fresh_when_structured_artifact_is_on_disk(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "prices", "prices_yahoo", NOW, {"policy_days": 1})
    (tmp_ticker_dir / "structured" / "prices_yahoo.json").write_text("{}", encoding="utf-8")
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == []


def test_fresh_when_source_document_is_on_disk(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "news", "2026-07-30_news_yahoo", NOW, {"policy_days": 5})
    (tmp_ticker_dir / "sources" / "2026-07-30_news_yahoo.md").write_text("x", encoding="utf-8")
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == []


def test_archived_source_still_counts_as_present(tmp_ticker_dir: Path):
    """Ids resolve through `resolve_source`, which sees `sources/archive/`
    too (§5) — a superseded document is still on disk, so its kind is not
    "missing", only aged by its time policy."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "news", "2026-07-30_news_yahoo", NOW, {"policy_days": 5})
    (tmp_ticker_dir / "sources" / "archive" / "2026-07-30_news_yahoo_2026-07-31.md").write_text(
        "x", encoding="utf-8")
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == []


def test_fresh_when_derived_artifact_is_on_disk(tmp_ticker_dir: Path):
    """`peers_candidates` is a prefetch data kind whose artifacts are silver
    and live under `derived/peers/` (§7, §13), so the on-disk check has to
    look there as well."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "peers_candidates", "peers_candidates", NOW, {"policy_days": 90})
    (tmp_ticker_dir / "derived" / "peers" / "peers_candidates.json").write_text(
        "{}", encoding="utf-8")
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == []


def test_one_missing_id_among_many_makes_the_kind_stale(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "financials", ["income_statement_yahoo", "key_ratios_computed"],
                 NOW, {"policy": "on_earnings"})
    (tmp_ticker_dir / "structured" / "income_statement_yahoo.json").write_text(
        "{}", encoding="utf-8")
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == ["financials"]


def test_missing_artifact_is_not_double_reported(tmp_ticker_dir: Path):
    """A kind that is both time-expired and missing from disk appears once."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_fetch(state, "prices", "prices_yahoo", NOW - timedelta(days=2), {"policy_days": 1})
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == ["prices"]


# --- derived block --------------------------------------------------------

def test_record_derived_stamps(tmp_ticker_dir: Path):
    """§7: silver lifecycle state lives under `derived{}` with a singular
    `current_id`, an `updated_at`, and stamped `derived_from` references."""
    state = init_state(tmp_ticker_dir, "PANW")
    refs = [
        {"id": "peers_ranked", "generated_at": "2026-07-29T00:00:00+00:00"},
        {"id": "peers_candidates", "fetched_at": "2026-07-28T00:00:00+00:00"},
    ]
    record_derived(state, "peers_selected", "peers_selected", NOW, refs)
    entry = state["derived"]["peers_selected"]
    assert entry["current_id"] == "peers_selected"
    assert entry["updated_at"] == NOW.isoformat()
    assert entry["derived_from"] == refs


def test_record_derived_rejects_unstamped_references(tmp_ticker_dir: Path):
    """An unstamped reference defeats the whole point of the block: §7 uses
    the timestamp to detect that a mutable structured id was refetched."""
    state = init_state(tmp_ticker_dir, "PANW")
    with pytest.raises(ValueError):
        record_derived(state, "peers_selected", "peers_selected", NOW, [{"id": "peers_ranked"}])
    with pytest.raises(ValueError):
        record_derived(state, "peers_selected", "peers_selected", NOW, ["peers_ranked"])


def test_derived_entries_are_not_aged_by_stale_kinds(tmp_ticker_dir: Path):
    """`stale` reports bronze data kinds only (§10.1); silver staleness is
    `invalidate`'s job (§10.2)."""
    state = init_state(tmp_ticker_dir, "PANW")
    record_derived(state, "peers_selected", "peers_selected", NOW - timedelta(days=999),
                   [{"id": "peers_candidates", "fetched_at": NOW.isoformat()}])
    assert stale_kinds(state, NOW, ticker_dir=tmp_ticker_dir) == []


# --- report bookkeeping ---------------------------------------------------

def test_mark_section_dirty_dedupes(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    mark_section_dirty(state, "competitive")
    mark_section_dirty(state, "competitive")
    assert state["report"]["sections_dirty"] == ["competitive"]
