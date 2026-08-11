"""Subscription invalidation: evidence that ARRIVED (spec §10.3, §14.0, §24).

§24 requires the two paths be tested separately, and they detect genuinely
different things. Dependency asks "was this replaced?"; subscription asks "did a
later period show up?". A new 10-Q or transcript supersedes nothing at all — it
is simply the next quarter — so no supersede chain exists to follow, and the
signal is instead the section's declared `subscribes_to` set plus the question's
own `answered_at`.

This is also the only path that revives a `deferred` question (§14.0): deferral
is a statement about the evidence available so far, and new subscribed evidence
is exactly the reason to try again.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sra
from lib.invalidate import apply_invalidation, compute_invalidation
from lib.provenance import SourceMeta, write_source
from lib.questions import add_questions, load_questions, mark_answered, question_hash
from lib.research import MAX_ATTEMPTS
from lib.sections import load_sections
from lib.statefile import init_state, load_state, record_fetch, save_state
from lib.wiki import read_page, write_page

DAY1 = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
DAY2 = DAY1 + timedelta(days=90)

CFG = load_sections()


def a_filing(ticker_dir: Path, sid: str, at: datetime, *, kind_key: str = "filings",
             state: dict | None = None) -> str:
    """A bronze filing, registered under a data kind so §10.3 can find its kind."""
    write_source(ticker_dir, SourceMeta(
        id=sid, ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/x", fetched_at=at.isoformat(),
        as_of=at.date().isoformat(), title="10-Q",
        fetch_tool="lib/fetchers/edgar.py", fetch_cmd="x"), "Body.",
        today=at.date())
    if state is not None:
        record_fetch(state, kind_key, sid, at, {"policy_days": 90})
        save_state(ticker_dir, state)
    return sid


def answered(ticker_dir: Path, text: str, sources: list[str], at: datetime,
             section: str = "valuation") -> str:
    add_questions(ticker_dir, section, [text])
    qhash = question_hash(section, text)
    mark_answered(ticker_dir, qhash, sources, now=at)
    return qhash


def status_of(ticker_dir: Path, qhash: str) -> str:
    return next(q for q in load_questions(ticker_dir) if q["hash"] == qhash)["status"]


def deferred(ticker_dir: Path, text: str, section: str = "valuation") -> str:
    from lib.questions import record_attempt

    add_questions(ticker_dir, section, [text])
    qhash = question_hash(section, text)
    for _ in range(MAX_ATTEMPTS):
        record_attempt(ticker_dir, qhash)
    assert status_of(ticker_dir, qhash) == "deferred"
    return qhash


# --- the arrival rule ------------------------------------------------------

def test_a_new_period_filing_reopens_a_question_answered_before_it(
        tmp_ticker_dir: Path):
    """§10.3: it supersedes nothing — there is no chain to follow, only the
    section's subscribed kinds and the question's answered_at."""
    state = init_state(tmp_ticker_dir, "PANW")
    old = a_filing(tmp_ticker_dir, "2026-05-21_sec_10q", DAY1, state=state)
    qhash = answered(tmp_ticker_dir, "What is FCF?", [old], at=DAY1)
    a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert [r["hash"] for r in report.reopened_questions] == [qhash]
    assert report.reopened_questions[0]["cause"] == "subscription"
    assert report.reopened_questions[0]["evidence_id"] == "2026-08-21_sec_10q"


def test_a_question_answered_after_the_new_artifact_does_not_reopen(
        tmp_ticker_dir: Path):
    """The whole point of comparing against `answered_at`: an answer that
    already saw the new evidence is still current."""
    state = init_state(tmp_ticker_dir, "PANW")
    sid = a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)
    answered(tmp_ticker_dir, "q?", [sid], at=DAY2 + timedelta(days=1))
    assert compute_invalidation(tmp_ticker_dir, CFG).reopened_questions == []


def test_an_unsubscribed_kind_does_not_reopen(tmp_ticker_dir: Path):
    """`profile` is not in valuation's subscribes_to, so a refreshed profile is
    not a reason to re-answer a valuation question."""
    state = init_state(tmp_ticker_dir, "PANW")
    old = a_filing(tmp_ticker_dir, "2026-05-21_sec_10q", DAY1, state=state)
    answered(tmp_ticker_dir, "q?", [old], at=DAY1)
    a_filing(tmp_ticker_dir, "2026-08-21_profile", DAY2, kind_key="profile",
             state=state)
    assert compute_invalidation(tmp_ticker_dir, CFG).reopened_questions == []


def test_a_section_subscribing_to_the_kind_reopens_while_another_does_not(
        tmp_ticker_dir: Path):
    """`calendar` is subscribed by risk_news and not by valuation."""
    state = init_state(tmp_ticker_dir, "PANW")
    old = a_filing(tmp_ticker_dir, "2026-05-21_sec_10q", DAY1, state=state)
    val = answered(tmp_ticker_dir, "valuation q?", [old], at=DAY1,
                   section="valuation")
    risk = answered(tmp_ticker_dir, "risk q?", [old], at=DAY1, section="risk_news")
    a_filing(tmp_ticker_dir, "2026-08-21_calendar", DAY2, kind_key="calendar",
             state=state)

    reopened = {r["hash"] for r in
                compute_invalidation(tmp_ticker_dir, CFG).reopened_questions}
    assert risk in reopened
    assert val not in reopened


def test_the_new_artifact_is_reported_as_new_bronze(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    old = a_filing(tmp_ticker_dir, "2026-05-21_sec_10q", DAY1, state=state)
    answered(tmp_ticker_dir, "q?", [old], at=DAY1)
    a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)
    assert "2026-08-21_sec_10q" in compute_invalidation(
        tmp_ticker_dir, CFG).new_bronze


# --- deferred revival ------------------------------------------------------

def test_new_subscribed_evidence_revives_a_deferred_question(tmp_ticker_dir: Path):
    """§14.0/§24: "a deferred question returns to open when invalidate --apply
    sees new bronze of a subscribed kind"."""
    state = init_state(tmp_ticker_dir, "PANW")
    qhash = deferred(tmp_ticker_dir, "unanswerable so far?")
    a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert report.revived_deferred == [qhash]

    apply_invalidation(tmp_ticker_dir, report)
    assert status_of(tmp_ticker_dir, qhash) == "open"


def test_a_deferred_question_keeps_its_attempt_count_when_revived(
        tmp_ticker_dir: Path):
    """Revival is a new chance, not amnesia: if the count reset, a truly
    unanswerable question would cycle forever."""
    state = init_state(tmp_ticker_dir, "PANW")
    qhash = deferred(tmp_ticker_dir, "unanswerable?")
    a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)
    apply_invalidation(tmp_ticker_dir, compute_invalidation(tmp_ticker_dir, CFG))
    row = next(q for q in load_questions(tmp_ticker_dir) if q["hash"] == qhash)
    assert row["attempts"] == MAX_ATTEMPTS


def test_a_deferred_question_with_no_new_evidence_stays_deferred(
        tmp_ticker_dir: Path):
    init_state(tmp_ticker_dir, "PANW")
    qhash = deferred(tmp_ticker_dir, "unanswerable?")
    assert compute_invalidation(tmp_ticker_dir, CFG).revived_deferred == []
    assert status_of(tmp_ticker_dir, qhash) == "deferred"


def test_a_dropped_question_is_never_revived(tmp_ticker_dir: Path):
    """§14.1 makes `dropped` an explicit decision by a synthesizer; new evidence
    does not overturn it."""
    from lib.questions import drop_question

    state = init_state(tmp_ticker_dir, "PANW")
    add_questions(tmp_ticker_dir, "valuation", ["out of scope?"])
    qhash = question_hash("valuation", "out of scope?")
    drop_question(tmp_ticker_dir, qhash)
    a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)

    report = compute_invalidation(tmp_ticker_dir, CFG)
    assert report.revived_deferred == []
    assert [r["hash"] for r in report.reopened_questions] == []


# --- apply is idempotent ---------------------------------------------------

def test_apply_is_idempotent(tmp_ticker_dir: Path):
    """§24: a second --apply over the same tree must change nothing."""
    state = init_state(tmp_ticker_dir, "PANW")
    old = a_filing(tmp_ticker_dir, "2026-05-21_sec_10q", DAY1, state=state)
    answered(tmp_ticker_dir, "q?", [old], at=DAY1)
    write_page(tmp_ticker_dir, "valuation",
               {"built_from": [{"id": old, "fetched_at": DAY1.isoformat()}]}, "N.")
    a_filing(tmp_ticker_dir, "2026-08-21_sec_10q", DAY2, state=state)

    apply_invalidation(tmp_ticker_dir, compute_invalidation(tmp_ticker_dir, CFG))
    ledger = (tmp_ticker_dir / "research" / "questions.json").read_bytes()
    state_bytes = (tmp_ticker_dir / ".state.json").read_bytes()
    page = (tmp_ticker_dir / "wiki" / "valuation.md").read_bytes()

    apply_invalidation(tmp_ticker_dir, compute_invalidation(tmp_ticker_dir, CFG))
    assert (tmp_ticker_dir / "research" / "questions.json").read_bytes() == ledger
    assert (tmp_ticker_dir / ".state.json").read_bytes() == state_bytes
    assert (tmp_ticker_dir / "wiki" / "valuation.md").read_bytes() == page


def test_sections_dirty_does_not_accumulate_duplicates(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    old = a_filing(tmp_ticker_dir, "2026-05-21_sec_10q", DAY1, state=state)
    write_page(tmp_ticker_dir, "valuation",
               {"built_from": [{"id": old, "fetched_at": DAY1.isoformat()}]}, "N.")
    write_source(tmp_ticker_dir, SourceMeta(
        id="2026-08-22_sec_10q", ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/y", fetched_at=DAY2.isoformat(),
        as_of=DAY2.date().isoformat(), title="10-Q",
        fetch_tool="lib/fetchers/edgar.py", fetch_cmd="x", supersedes=old),
        "New.", today=DAY2.date())

    for _ in range(3):
        apply_invalidation(tmp_ticker_dir, compute_invalidation(tmp_ticker_dir, CFG))
    assert load_state(tmp_ticker_dir)["report"]["sections_dirty"] == ["valuation"]


# --- the CLI ---------------------------------------------------------------

def _cli_tree(tmp_path: Path) -> Path:
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    state = load_state(d)
    old = a_filing(d, "2026-05-21_sec_10q", DAY1, state=state)
    answered(d, "q?", [old], at=DAY1)
    a_filing(d, "2026-08-21_sec_10q", DAY2, state=state)
    return d


def test_cli_is_dry_run_by_default(tmp_path: Path, capsys):
    d = _cli_tree(tmp_path)
    before = (d / "research" / "questions.json").read_bytes()
    capsys.readouterr()
    assert sra.main(["invalidate", "PANW", "--data-root", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["reopened_questions"]
    assert (d / "research" / "questions.json").read_bytes() == before


def test_cli_apply_executes(tmp_path: Path, capsys):
    d = _cli_tree(tmp_path)
    capsys.readouterr()
    assert sra.main(["invalidate", "PANW", "--data-root", str(tmp_path),
                     "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert load_questions(d)[0]["status"] == "reopened"


def test_cli_reports_the_six_output_lines(tmp_path: Path, capsys):
    """§10.2 names exactly what the dry run must distinguish."""
    _cli_tree(tmp_path)
    capsys.readouterr()
    sra.main(["invalidate", "PANW", "--data-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"applied", "new_bronze", "reopened_questions",
                            "revived_deferred", "dirty_wiki_pages",
                            "dirty_report_sections"}


def test_cli_needs_an_initialized_ticker(tmp_path: Path):
    assert sra.main(["invalidate", "PANW", "--data-root", str(tmp_path)]) == 1
