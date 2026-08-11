"""The durable question ledger (spec §14, §14.0, §14.1, §24).

`research/questions.json` is per-ticker state that ACCUMULATES ACROSS RUNS, not
per-run scratch: what one build could not answer is what the next build starts
from. Nearly every property here follows from that — identity has to collapse a
re-proposed question instead of duplicating it, `attempts` has to survive the
collapse (or the deferral floor never fires), and nothing may be refused for
volume.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.provenance import SourceMeta, StructuredMeta, write_derived, write_source, write_structured
from lib.questions import (
    STATUSES,
    add_questions,
    drop_question,
    load_questions,
    ledger_path,
    mark_answered,
    open_questions,
    question_hash,
    record_attempt,
)
from lib.research import MAX_ATTEMPTS

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def a_source(ticker_dir: Path, sid: str = "2026-05-21_sec_10q") -> str:
    write_source(ticker_dir, SourceMeta(
        id=sid, ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/x", fetched_at=NOW.isoformat(),
        as_of=NOW.date().isoformat(), title="10-Q",
        fetch_tool="lib/fetchers/edgar.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds filings"), "Body.")
    return sid


def a_structured(ticker_dir: Path, sid: str = "profile_yahoo") -> str:
    write_structured(ticker_dir, StructuredMeta(
        id=sid, ticker="PANW", producer="fetch", title="Profile",
        source="Yahoo Finance", url="https://finance.yahoo.com/x",
        provider_tool="yfinance", fetch_cmd="uv run python sra.py prefetch PANW",
        fetched_at=NOW.isoformat(), as_of=NOW.date().isoformat()), {"a": 1})
    return sid


def a_derived(ticker_dir: Path, sid: str = "peers_selected") -> str:
    write_derived(ticker_dir, StructuredMeta(
        id=sid, ticker="PANW", producer="model", title="Peers", source="sra-rater",
        generated_at=NOW.isoformat(), as_of=NOW.date().isoformat(),
        derived_from=["profile_yahoo"]), {"peers": []}, namespace="peers")
    return sid


# --- identity --------------------------------------------------------------

def test_question_hash_is_the_spec_definition():
    """§14: sha1(f"{section}|{question.strip().lower()}").hexdigest()[:10]."""
    import hashlib

    expected = hashlib.sha1(b"valuation|what is fcf?").hexdigest()[:10]
    assert question_hash("valuation", "  What Is FCF?  ") == expected
    assert len(expected) == 10


def test_the_section_is_part_of_the_identity():
    """§14: "The same question may therefore exist independently in two
    sections"."""
    assert question_hash("valuation", "q") != question_hash("risk_news", "q")


def test_statuses_are_the_five_the_spec_names():
    assert set(STATUSES) == {"open", "answered", "dropped", "deferred", "reopened"}


# --- add-questions ---------------------------------------------------------

def test_n_occurrences_produce_n_entries_with_the_calls_metadata(tmp_ticker_dir: Path):
    """§24 bullet 1: repeatable --question, all carrying the call's section,
    round and origin."""
    add_questions(tmp_ticker_dir, "competitive",
                  ["Is pricing under pressure?", "Has SASE win rate moved?"],
                  round_=2, origin="critic")
    rows = load_questions(tmp_ticker_dir)
    assert len(rows) == 2
    assert all(r["section"] == "competitive" for r in rows)
    assert all(r["round"] == 2 for r in rows)
    assert all(r["origin"] == "critic" for r in rows)
    assert all(r["status"] == "open" for r in rows)
    assert all(r["attempts"] == 0 for r in rows)


def test_the_entry_carries_every_field_the_spec_names(tmp_ticker_dir: Path):
    add_questions(tmp_ticker_dir, "valuation", ["What is FCF?"])
    row = load_questions(tmp_ticker_dir)[0]
    assert set(row) >= {"hash", "question", "section", "status", "origin",
                        "attempts", "round", "answer_source_ids", "answer_artifacts"}


def test_a_repeat_within_one_call_collapses(tmp_ticker_dir: Path):
    """§24 bullet 2, first half."""
    add_questions(tmp_ticker_dir, "valuation", ["What is FCF?", "what is fcf?  "])
    assert len(load_questions(tmp_ticker_dir)) == 1


def test_a_re_add_by_a_later_phase_does_not_reset_attempts(tmp_ticker_dir: Path):
    """§24 bullet 2, second half — and the reason it matters: if a re-proposal
    reset the counter, the §14.0 deferral floor would never fire for exactly the
    questions that keep coming back."""
    add_questions(tmp_ticker_dir, "valuation", ["What is FCF?"], origin="seed")
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    record_attempt(tmp_ticker_dir, qhash)
    record_attempt(tmp_ticker_dir, qhash)

    add_questions(tmp_ticker_dir, "valuation", ["What is FCF?"], origin="critic")
    rows = load_questions(tmp_ticker_dir)
    assert len(rows) == 1
    assert rows[0]["attempts"] == 2
    assert rows[0]["origin"] == "seed"          # the original raiser is kept


def test_the_same_text_in_two_sections_is_two_entries(tmp_ticker_dir: Path):
    """§24 bullet 3."""
    add_questions(tmp_ticker_dir, "valuation", ["Same text"])
    add_questions(tmp_ticker_dir, "risk_news", ["Same text"])
    rows = load_questions(tmp_ticker_dir)
    assert len(rows) == 2
    assert {r["section"] for r in rows} == {"valuation", "risk_news"}


def test_any_volume_is_accepted(tmp_ticker_dir: Path):
    """§24 bullet 4: "nothing is refused for count". A large backlog is a
    scheduling fact, never an error."""
    texts = [f"Question number {i}?" for i in range(500)]
    add_questions(tmp_ticker_dir, "valuation", texts)
    assert len(load_questions(tmp_ticker_dir)) == 500


def test_add_questions_reports_the_resulting_open_count(tmp_ticker_dir: Path):
    add_questions(tmp_ticker_dir, "valuation", ["a?", "b?"])
    result = add_questions(tmp_ticker_dir, "valuation", ["b?", "c?"])
    assert result["added"] == 1
    assert result["open"] == 3


def test_blank_questions_are_ignored(tmp_ticker_dir: Path):
    add_questions(tmp_ticker_dir, "valuation", ["", "   ", "real?"])
    assert len(load_questions(tmp_ticker_dir)) == 1


def test_a_hash_collision_on_a_different_pair_is_refused(tmp_ticker_dir: Path,
                                                         monkeypatch):
    """§14: "If the 10-character hash collides for different (section, question)
    pairs, add-questions must refuse the collision and report both entries"."""
    # Both texts must hash alike for this to BE a collision, so the digest is
    # forced to a constant before either is added.
    monkeypatch.setattr("lib.questions.question_hash", lambda s, q: "collide123")
    add_questions(tmp_ticker_dir, "valuation", ["first question?"])
    with pytest.raises(ValueError) as exc:
        add_questions(tmp_ticker_dir, "valuation", ["a totally different one?"])
    assert "first question?" in str(exc.value)
    assert "a totally different one?" in str(exc.value)
    # ...and the refusal leaves the ledger alone rather than half-merging.
    assert len(load_questions(tmp_ticker_dir)) == 1


def test_the_ledger_lives_under_research_not_dot_research(tmp_ticker_dir: Path):
    """§14: data/<T>/research/questions.json. EXP's hidden `.research/` is
    renamed — §4's tree has no dot-directory."""
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    assert ledger_path(tmp_ticker_dir) == tmp_ticker_dir / "research" / "questions.json"
    assert ledger_path(tmp_ticker_dir).exists()
    assert not (tmp_ticker_dir / ".research").exists()


# --- attempts and deferral -------------------------------------------------

def test_record_attempt_defers_exactly_at_max_attempts(tmp_ticker_dir: Path):
    """§24 bullet 5. Deterministic bookkeeping, not judgment."""
    add_questions(tmp_ticker_dir, "valuation", ["unanswerable?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    for i in range(1, MAX_ATTEMPTS):
        assert record_attempt(tmp_ticker_dir, qhash) == "open", i
    assert record_attempt(tmp_ticker_dir, qhash) == "deferred"
    row = load_questions(tmp_ticker_dir)[0]
    assert row["attempts"] == MAX_ATTEMPTS


def test_a_question_answered_before_the_floor_never_defers(tmp_ticker_dir: Path):
    """§14.1: "A question answered on its second try keeps its count and is
    never deferred"."""
    sid = a_source(tmp_ticker_dir)
    add_questions(tmp_ticker_dir, "valuation", ["answerable?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    record_attempt(tmp_ticker_dir, qhash)
    mark_answered(tmp_ticker_dir, qhash, [sid])

    row = load_questions(tmp_ticker_dir)[0]
    assert row["status"] == "answered"
    assert row["attempts"] == 1          # the count is kept, not cleared


def test_record_attempt_does_not_defer_an_answered_question(tmp_ticker_dir: Path):
    """Only `open` defers (§14.1's table): a late attempt against a closed
    question must not reopen the deferral path."""
    sid = a_source(tmp_ticker_dir)
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    mark_answered(tmp_ticker_dir, qhash, [sid])
    for _ in range(MAX_ATTEMPTS + 1):
        record_attempt(tmp_ticker_dir, qhash)
    assert load_questions(tmp_ticker_dir)[0]["status"] == "answered"


def test_record_attempt_on_an_unknown_hash_raises(tmp_ticker_dir: Path):
    with pytest.raises(KeyError):
        record_attempt(tmp_ticker_dir, "nosuchhash")


# --- fan-out selection -----------------------------------------------------

def test_fan_out_selects_open_only(tmp_ticker_dir: Path):
    """§24 bullet 6: deferred, answered and dropped are not dispatched.

    `reopened` IS dispatched — §10.2 reopens a question precisely so it gets
    re-answered against the new evidence.
    """
    sid = a_source(tmp_ticker_dir)
    add_questions(tmp_ticker_dir, "valuation",
                  ["open one?", "answered one?", "dropped one?", "deferred one?",
                   "reopened one?"])
    by_text = {r["question"]: r["hash"] for r in load_questions(tmp_ticker_dir)}
    mark_answered(tmp_ticker_dir, by_text["answered one?"], [sid])
    drop_question(tmp_ticker_dir, by_text["dropped one?"])
    for _ in range(MAX_ATTEMPTS):
        record_attempt(tmp_ticker_dir, by_text["deferred one?"])
    mark_answered(tmp_ticker_dir, by_text["reopened one?"], [sid])
    rows = load_questions(tmp_ticker_dir)
    for row in rows:
        if row["question"] == "reopened one?":
            row["status"] = "reopened"
    ledger_path(tmp_ticker_dir).write_text(json.dumps(rows), encoding="utf-8")

    dispatched = {q["question"] for q in open_questions(tmp_ticker_dir)}
    assert dispatched == {"open one?", "reopened one?"}


def test_open_questions_filters_by_section(tmp_ticker_dir: Path):
    add_questions(tmp_ticker_dir, "valuation", ["v?"])
    add_questions(tmp_ticker_dir, "risk_news", ["r?"])
    assert [q["question"] for q in open_questions(tmp_ticker_dir, "valuation")] == ["v?"]


# --- mark-answered ---------------------------------------------------------

def test_mark_answered_stamps_bronze_sources(tmp_ticker_dir: Path):
    """§14: answer_source_ids holds STAMPED bronze ids — the stamp is what lets
    `invalidate` (§10.2) notice the evidence was replaced."""
    sid = a_source(tmp_ticker_dir)
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    row = mark_answered(tmp_ticker_dir, qhash, [sid],
                        artifacts=["2026-08-02_research_answer_r1-fcf"], now=NOW)

    assert row["status"] == "answered"
    assert row["answered_at"] == NOW.isoformat()
    assert row["answer_source_ids"] == [{"id": sid, "fetched_at": NOW.isoformat()}]
    assert row["answer_artifacts"] == ["2026-08-02_research_answer_r1-fcf"]


def test_mark_answered_accepts_a_structured_bronze_id(tmp_ticker_dir: Path):
    sid = a_structured(tmp_ticker_dir)
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    row = mark_answered(tmp_ticker_dir, qhash, [sid])
    assert row["answer_source_ids"][0]["id"] == sid


def test_mark_answered_rejects_a_silver_id(tmp_ticker_dir: Path):
    """§14.1: "--sources accepts bronze ids only". A citation terminating in
    derived/ is the §1.2 defect: model-mediated content answering for evidence."""
    a_structured(tmp_ticker_dir)                 # so peers_selected can derive
    sid = a_derived(tmp_ticker_dir)
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    with pytest.raises(ValueError) as exc:
        mark_answered(tmp_ticker_dir, qhash, [sid])
    assert sid in str(exc.value)
    assert load_questions(tmp_ticker_dir)[0]["status"] == "open"


def test_mark_answered_rejects_an_unresolvable_id(tmp_ticker_dir: Path):
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    with pytest.raises(ValueError):
        mark_answered(tmp_ticker_dir, qhash, ["2026-01-01_sec_10k"])


def test_mark_answered_with_no_sources_leaves_the_question_open(tmp_ticker_dir: Path):
    """§14.1: "If supporting URL fetches fail and no bronze evidence remains,
    the question stays open." Silence never means dropped."""
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    with pytest.raises(ValueError):
        mark_answered(tmp_ticker_dir, qhash, [])
    assert load_questions(tmp_ticker_dir)[0]["status"] == "open"


def test_mark_answered_merges_sources_without_duplicating(tmp_ticker_dir: Path):
    first = a_source(tmp_ticker_dir)
    second = a_source(tmp_ticker_dir, "2026-05-22_sec_10q")
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    mark_answered(tmp_ticker_dir, qhash, [first])
    row = mark_answered(tmp_ticker_dir, qhash, [first, second])
    assert [s["id"] for s in row["answer_source_ids"]] == [first, second]


def test_mark_answered_resolves_an_archived_source(tmp_ticker_dir: Path):
    """A superseded source still answers for the citation that named it (§5)."""
    old = a_source(tmp_ticker_dir, "2026-05-21_sec_10q")
    write_source(tmp_ticker_dir, SourceMeta(
        id="2026-08-21_sec_10q", ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/y", fetched_at=NOW.isoformat(),
        as_of=NOW.date().isoformat(), title="10-Q",
        fetch_tool="lib/fetchers/edgar.py", fetch_cmd="x", supersedes=old), "New.")
    add_questions(tmp_ticker_dir, "valuation", ["q?"])
    qhash = load_questions(tmp_ticker_dir)[0]["hash"]
    assert mark_answered(tmp_ticker_dir, qhash, [old])["status"] == "answered"


# --- durability ------------------------------------------------------------

def test_the_ledger_round_trips_as_json(tmp_ticker_dir: Path):
    add_questions(tmp_ticker_dir, "valuation", ["q?"], origin="user")
    raw = json.loads(ledger_path(tmp_ticker_dir).read_text(encoding="utf-8"))
    assert isinstance(raw, list) and raw[0]["origin"] == "user"


def test_a_missing_ledger_reads_as_empty(tmp_ticker_dir: Path):
    assert load_questions(tmp_ticker_dir) == []
    assert open_questions(tmp_ticker_dir) == []
