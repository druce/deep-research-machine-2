"""Dependency invalidation: consumers of REPLACED evidence (spec §10.2, §24).

§24 requires the two invalidation paths be tested separately; this is the
replacement path. A source is replaced when a current source names it in
`supersedes`; a structured artifact is replaced when its producer timestamp is
newer than the stamp a consumer recorded when it used it.

That second rule is why §7 insists derivation references are STAMPED. Structured
bronze ids are overwritten in place — only `sources/` is immutable — so without
the timestamp there is no way to tell a refetched `profile_yahoo` from the one a
question was answered against, and the invalidation would simply never fire.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.invalidate import InvalidationReport, apply_invalidation, compute_invalidation
from lib.provenance import SourceMeta, StructuredMeta, write_source, write_structured
from lib.questions import add_questions, load_questions, mark_answered, question_hash
from lib.sections import load_sections
from lib.statefile import init_state, load_state, save_state
from lib.wiki import read_page, write_page

DAY1 = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
DAY2 = DAY1 + timedelta(days=10)

CFG = load_sections()


def a_source(ticker_dir: Path, sid: str, *, at: datetime = DAY1,
             supersedes: str | None = None) -> str:
    write_source(ticker_dir, SourceMeta(
        id=sid, ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/x", fetched_at=at.isoformat(),
        as_of=at.date().isoformat(), title="10-Q",
        fetch_tool="lib/fetchers/edgar.py", fetch_cmd="x",
        supersedes=supersedes), "Body.", today=at.date())
    return sid


def a_structured(ticker_dir: Path, sid: str = "profile_yahoo", *,
                 at: datetime = DAY1) -> str:
    write_structured(ticker_dir, StructuredMeta(
        id=sid, ticker="PANW", producer="fetch", title="Profile",
        source="Yahoo Finance", url="https://finance.yahoo.com/x",
        provider_tool="yfinance", fetch_cmd="x",
        fetched_at=at.isoformat(), as_of=at.date().isoformat()), {"a": 1})
    return sid


def an_answered_question(ticker_dir: Path, text: str, sources: list[str],
                         section: str = "valuation", *,
                         at: datetime = DAY1) -> str:
    add_questions(ticker_dir, section, [text])
    qhash = question_hash(section, text)
    mark_answered(ticker_dir, qhash, sources, now=at)
    return qhash


def a_wiki_page(ticker_dir: Path, page: str, built_from: list[dict]) -> None:
    write_page(ticker_dir, page, {"section": page, "built_from": built_from},
               "Notes.")


def status_of(ticker_dir: Path, qhash: str) -> str:
    return next(q for q in load_questions(ticker_dir) if q["hash"] == qhash)["status"]


# --- nothing to do ---------------------------------------------------------

def test_untouched_evidence_produces_an_empty_report(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    sid = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    an_answered_question(tmp_ticker_dir, "q?", [sid])
    a_wiki_page(tmp_ticker_dir, "valuation",
                [{"id": sid, "fetched_at": DAY1.isoformat()}])

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert report == InvalidationReport([], [], [], [], [])
    assert report.is_empty()


def test_an_empty_tree_produces_an_empty_report(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    assert compute_invalidation(tmp_ticker_dir, CFG).is_empty()


# --- replaced source -------------------------------------------------------

def test_superseding_a_cited_source_reopens_its_question(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    qhash = an_answered_question(tmp_ticker_dir, "What is FCF?", [old])
    new = a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert new in report.new_bronze
    assert report.reopened_questions == [
        {"hash": qhash, "section": "valuation", "cause": "dependency",
         "evidence_id": old}]


def test_superseding_a_cited_source_dirties_its_wiki_page(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    a_wiki_page(tmp_ticker_dir, "valuation",
                [{"id": old, "fetched_at": DAY1.isoformat()}])
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert report.dirty_wiki_pages == ["valuation"]
    assert report.dirty_report_sections == ["valuation"]


def test_a_page_built_from_untouched_evidence_stays_clean(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    other = a_source(tmp_ticker_dir, "2026-05-21_sec_8k")
    a_wiki_page(tmp_ticker_dir, "valuation",
                [{"id": other, "fetched_at": DAY1.isoformat()}])
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    assert compute_invalidation(tmp_ticker_dir, CFG).dirty_wiki_pages == []


def test_an_open_question_is_not_reopened(tmp_ticker_dir: Path):
    """`reopened` is a transition out of `answered` (§14.1); an already-open
    question has nothing to reopen."""
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    add_questions(tmp_ticker_dir, "valuation", ["still open?"])
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)
    assert compute_invalidation(tmp_ticker_dir, CFG).reopened_questions == []


# --- replaced structured artifact -----------------------------------------

def test_a_refetched_structured_artifact_reopens_its_question(tmp_ticker_dir: Path):
    """§10.2: replaced when the producer timestamp is newer than the consumer's
    stamp. Structured bronze is overwritten in place, so the stamp is the only
    evidence that a refetch happened."""
    init_state(tmp_ticker_dir, "PANW")
    sid = a_structured(tmp_ticker_dir, at=DAY1)
    qhash = an_answered_question(tmp_ticker_dir, "q?", [sid])
    a_structured(tmp_ticker_dir, at=DAY2)          # same id, refetched

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert sid in report.new_bronze
    assert [r["hash"] for r in report.reopened_questions] == [qhash]
    assert report.reopened_questions[0]["cause"] == "dependency"


def test_a_refetched_structured_artifact_dirties_its_wiki_page(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    sid = a_structured(tmp_ticker_dir, at=DAY1)
    a_wiki_page(tmp_ticker_dir, "financial",
                [{"id": sid, "fetched_at": DAY1.isoformat()}])
    a_structured(tmp_ticker_dir, at=DAY2)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert report.dirty_wiki_pages == ["financial"]
    assert report.dirty_report_sections == ["financial"]


def test_a_structured_artifact_at_the_same_stamp_is_not_replaced(tmp_ticker_dir: Path):
    """Equal is not newer: a re-run that rewrote nothing must not invalidate."""
    init_state(tmp_ticker_dir, "PANW")
    sid = a_structured(tmp_ticker_dir, at=DAY1)
    an_answered_question(tmp_ticker_dir, "q?", [sid])
    assert compute_invalidation(tmp_ticker_dir, CFG).is_empty()


def test_a_stale_state_derived_reference_is_reported(tmp_ticker_dir: Path):
    """§10.2 names `derived.*.derived_from` as a consumer stamp too."""
    init_state(tmp_ticker_dir, "PANW")
    sid = a_structured(tmp_ticker_dir, at=DAY1)
    state = load_state(tmp_ticker_dir)
    state["derived"]["peers_selected"] = {
        "current_id": "peers_selected", "updated_at": DAY1.isoformat(),
        "derived_from": [{"id": sid, "fetched_at": DAY1.isoformat()}]}
    save_state(tmp_ticker_dir, state)
    a_structured(tmp_ticker_dir, at=DAY2)

    assert sid in compute_invalidation(tmp_ticker_dir, CFG).new_bronze


# --- dry run mutates nothing ----------------------------------------------

def test_compute_mutates_nothing(tmp_ticker_dir: Path):
    """§10.3: "invalidate is dry-run by default. Mutation requires --apply"."""
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    an_answered_question(tmp_ticker_dir, "q?", [old])
    a_wiki_page(tmp_ticker_dir, "valuation",
                [{"id": old, "fetched_at": DAY1.isoformat()}])
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    ledger = tmp_ticker_dir / "research" / "questions.json"
    state_path = tmp_ticker_dir / ".state.json"
    page = tmp_ticker_dir / "wiki" / "valuation.md"
    before = (ledger.read_bytes(), state_path.read_bytes(), page.read_bytes())

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert not report.is_empty()          # it found something...
    assert (ledger.read_bytes(), state_path.read_bytes(),
            page.read_bytes()) == before  # ...and changed nothing


# --- apply -----------------------------------------------------------------

def test_apply_performs_the_transitions(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    qhash = an_answered_question(tmp_ticker_dir, "q?", [old])
    a_wiki_page(tmp_ticker_dir, "valuation",
                [{"id": old, "fetched_at": DAY1.isoformat()}])
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    apply_invalidation(tmp_ticker_dir, report)

    assert status_of(tmp_ticker_dir, qhash) == "reopened"
    meta, _ = read_page(tmp_ticker_dir, "valuation")
    assert meta["dirty"] is True
    assert load_state(tmp_ticker_dir)["report"]["sections_dirty"] == ["valuation"]


def test_apply_does_not_restamp_the_wiki_pages_updated_at(tmp_ticker_dir: Path):
    """Marking a page dirty is bookkeeping ABOUT the page, not a write of it —
    restamping would claim the notes were revised when they were not."""
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    a_wiki_page(tmp_ticker_dir, "valuation",
                [{"id": old, "fetched_at": DAY1.isoformat()}])
    before = read_page(tmp_ticker_dir, "valuation")[0]["updated_at"]
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    apply_invalidation(tmp_ticker_dir, compute_invalidation(tmp_ticker_dir, CFG))
    assert read_page(tmp_ticker_dir, "valuation")[0]["updated_at"] == before


def test_apply_preserves_the_built_from_stamps(tmp_ticker_dir: Path):
    """Rewriting them would erase the timestamps invalidate reads next time."""
    init_state(tmp_ticker_dir, "PANW")
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    refs = [{"id": old, "fetched_at": DAY1.isoformat()}]
    a_wiki_page(tmp_ticker_dir, "valuation", refs)
    a_source(tmp_ticker_dir, "2026-08-21_sec_10q", at=DAY2, supersedes=old)

    apply_invalidation(tmp_ticker_dir, compute_invalidation(tmp_ticker_dir, CFG))
    assert read_page(tmp_ticker_dir, "valuation")[0]["built_from"] == refs


def test_apply_of_an_empty_report_is_a_no_op(tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    state_path = tmp_ticker_dir / ".state.json"
    before = state_path.read_bytes()
    apply_invalidation(tmp_ticker_dir, InvalidationReport([], [], [], [], []))
    assert state_path.read_bytes() == before
