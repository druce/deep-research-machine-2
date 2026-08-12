"""The question-ledger CLI: `questions`, `add-questions`, `mark-answered`,
`record-attempt` (spec §14, §14.1, §19, §24).

`add-questions` is the capture surface EVERY phase uses (§14.0) — a writer that
hits a gap, a critic, `sra-lint`, chart selection — so its contract is that
capture is cheap, idempotent, and never refused.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sra
from lib.provenance import SourceMeta, write_source
from lib.questions import load_questions, question_hash
from lib.research import MAX_ATTEMPTS

NOW = "2026-07-30T12:00:00+00:00"


def _init(tmp_path: Path) -> Path:
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    return tmp_path / "PANW"


def run(tmp_path: Path, *args) -> int:
    return sra.main([*args, "--data-root", str(tmp_path)])


def out(capsys) -> object:
    return json.loads(capsys.readouterr().out)


def a_source(ticker_dir: Path, sid: str = "2026-05-21_sec_10q") -> str:
    write_source(ticker_dir, SourceMeta(
        id=sid, ticker="PANW", kind="sec_filing", source="SEC EDGAR",
        url="https://www.sec.gov/x", fetched_at=NOW, as_of="2026-05-21",
        title="10-Q", fetch_tool="lib/fetchers/edgar.py", fetch_cmd="x"), "Body.")
    return sid


# --- add-questions ---------------------------------------------------------

def test_repeatable_question_flag(tmp_path: Path, capsys):
    """§14.1: --question is repeatable; each occurrence is one entry carrying
    the call's section and round."""
    d = _init(tmp_path)
    capsys.readouterr()
    assert run(tmp_path, "add-questions", "PANW", "--section", "competitive",
               "--question", "Is pricing under pressure?",
               "--question", "Has SASE win rate moved?",
               "--round", "2", "--origin", "critic") == 0
    assert out(capsys)["added"] == 2
    rows = load_questions(d)
    assert len(rows) == 2
    assert all(r["section"] == "competitive" and r["round"] == 2
               and r["origin"] == "critic" for r in rows)


def test_add_questions_reports_the_open_backlog(tmp_path: Path, capsys):
    """§14.1: "add-questions reports the resulting open count so the operator
    sees the backlog it implies, but the count is never an error"."""
    _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "a?")
    capsys.readouterr()
    assert run(tmp_path, "add-questions", "PANW", "--section", "valuation",
               "--question", "a?", "--question", "b?") == 0
    payload = out(capsys)
    assert payload["added"] == 1 and payload["open"] == 2


def test_a_large_batch_is_accepted(tmp_path: Path, capsys, tmp_path_factory):
    """§24: any volume is accepted and written; nothing is refused for count."""
    _init(tmp_path)
    args = []
    for i in range(200):
        args += ["--question", f"Question {i}?"]
    capsys.readouterr()
    assert run(tmp_path, "add-questions", "PANW", "--section", "valuation",
               *args) == 0
    assert out(capsys)["added"] == 200


def test_add_questions_from_file(tmp_path: Path, capsys):
    d = _init(tmp_path)
    qfile = tmp_path / "qs.txt"
    qfile.write_text("First question?\n\nSecond question?\n", encoding="utf-8")
    capsys.readouterr()
    assert run(tmp_path, "add-questions", "PANW", "--section", "valuation",
               "--from-file", str(qfile)) == 0
    assert out(capsys)["added"] == 2
    assert {r["question"] for r in load_questions(d)} == {
        "First question?", "Second question?"}


def test_add_questions_needs_a_source_of_questions(tmp_path: Path):
    _init(tmp_path)
    assert run(tmp_path, "add-questions", "PANW", "--section", "valuation") == 1


def test_add_questions_rejects_an_unknown_section(tmp_path: Path):
    """The section is half of a question's identity, so a typo would silently
    create a parallel ledger nothing ever dispatches."""
    _init(tmp_path)
    assert run(tmp_path, "add-questions", "PANW", "--section", "valuatoin",
               "--question", "q?") == 1


def test_add_questions_rejects_a_missing_file(tmp_path: Path):
    _init(tmp_path)
    assert run(tmp_path, "add-questions", "PANW", "--section", "valuation",
               "--from-file", str(tmp_path / "nope.txt")) == 1


# --- questions -------------------------------------------------------------

def test_questions_lists_the_ledger(tmp_path: Path, capsys):
    _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "a?")
    capsys.readouterr()
    assert run(tmp_path, "questions", "PANW") == 0
    assert [q["question"] for q in out(capsys)] == ["a?"]


def test_questions_filters_by_section_and_status(tmp_path: Path, capsys):
    d = _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "v?")
    run(tmp_path, "add-questions", "PANW", "--section", "risk_news",
        "--question", "r?")
    sid = a_source(d)
    qhash = question_hash("valuation", "v?")
    run(tmp_path, "mark-answered", "PANW", "--question-hash", qhash,
        "--sources", sid)

    capsys.readouterr()
    run(tmp_path, "questions", "PANW", "--section", "valuation")
    assert [q["question"] for q in out(capsys)] == ["v?"]

    run(tmp_path, "questions", "PANW", "--status", "open")
    assert [q["question"] for q in out(capsys)] == ["r?"]


def test_questions_on_an_empty_ledger(tmp_path: Path, capsys):
    _init(tmp_path)
    capsys.readouterr()
    assert run(tmp_path, "questions", "PANW") == 0
    assert out(capsys) == []


# --- mark-answered ---------------------------------------------------------

def test_mark_answered_records_stamped_sources(tmp_path: Path, capsys):
    d = _init(tmp_path)
    sid = a_source(d)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "q?")
    qhash = question_hash("valuation", "q?")
    capsys.readouterr()
    assert run(tmp_path, "mark-answered", "PANW", "--question-hash", qhash,
               "--sources", sid, "--artifacts", "2026-08-02_answer_r1") == 0
    row = load_questions(d)[0]
    assert row["status"] == "answered"
    assert row["answer_source_ids"] == [{"id": sid, "fetched_at": NOW}]
    assert row["answer_artifacts"] == ["2026-08-02_answer_r1"]


def test_mark_answered_accepts_comma_separated_sources(tmp_path: Path):
    d = _init(tmp_path)
    first = a_source(d)
    second = a_source(d, "2026-05-22_sec_10q")
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "q?")
    qhash = question_hash("valuation", "q?")
    assert run(tmp_path, "mark-answered", "PANW", "--question-hash", qhash,
               "--sources", f"{first},{second}") == 0
    assert len(load_questions(d)[0]["answer_source_ids"]) == 2


def test_mark_answered_rejects_a_non_bronze_source(tmp_path: Path):
    """§14.1: bronze ids only — exit 1, question untouched."""
    d = _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "q?")
    qhash = question_hash("valuation", "q?")
    assert run(tmp_path, "mark-answered", "PANW", "--question-hash", qhash,
               "--sources", "no_such_artifact") == 1
    assert load_questions(d)[0]["status"] == "open"


def test_mark_answered_rejects_an_unknown_hash(tmp_path: Path):
    d = _init(tmp_path)
    sid = a_source(d)
    assert run(tmp_path, "mark-answered", "PANW", "--question-hash", "deadbeef00",
               "--sources", sid) == 1


# --- record-attempt --------------------------------------------------------

def test_record_attempt_reports_status_and_defers_at_the_floor(tmp_path: Path,
                                                               capsys):
    """§20 defines record_attempt but §19's table has no command reaching it —
    this subcommand is that addition (plan Task 8.1)."""
    _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "unanswerable?")
    qhash = question_hash("valuation", "unanswerable?")

    for expected in range(1, MAX_ATTEMPTS):
        capsys.readouterr()
        assert run(tmp_path, "record-attempt", "PANW",
                   "--question-hash", qhash) == 0
        payload = out(capsys)
        assert payload == {"hash": qhash, "attempts": expected, "status": "open"}

    capsys.readouterr()
    run(tmp_path, "record-attempt", "PANW", "--question-hash", qhash)
    assert out(capsys)["status"] == "deferred"


def test_record_attempt_on_an_unknown_hash_exits_1(tmp_path: Path):
    _init(tmp_path)
    assert run(tmp_path, "record-attempt", "PANW",
               "--question-hash", "deadbeef00") == 1


def test_record_attempt_accepts_several_hashes(tmp_path: Path, capsys):
    _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "a?", "--question", "b?")
    a, b = question_hash("valuation", "a?"), question_hash("valuation", "b?")
    capsys.readouterr()
    assert run(tmp_path, "record-attempt", "PANW", "--question-hash", a,
               "--question-hash", b) == 0
    assert [r["hash"] for r in out(capsys)] == [a, b]


# --- drop-question ---------------------------------------------------------

def test_drop_question_marks_it_dropped(tmp_path: Path, capsys):
    """§14.1: only a synthesizer drops a question, and only as an explicit
    decision. §20 defines `drop_question` but §19's table has no command
    reaching it — this subcommand is that addition, on the same reasoning as
    `record-attempt`: a skill may not hand-edit the ledger (§3)."""
    d = _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "out of scope?")
    qhash = question_hash("valuation", "out of scope?")

    capsys.readouterr()
    assert run(tmp_path, "drop-question", "PANW", "--question-hash", qhash) == 0
    assert out(capsys) == {"hash": qhash, "status": "dropped"}
    assert [q["status"] for q in load_questions(d)] == ["dropped"]


def test_dropped_questions_are_not_dispatchable(tmp_path: Path, capsys):
    d = _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "keep?", "--question", "drop?")
    run(tmp_path, "drop-question", "PANW", "--question-hash",
        question_hash("valuation", "drop?"))

    from lib.questions import open_questions
    assert [q["question"] for q in open_questions(d)] == ["keep?"]


def test_drop_question_accepts_several_hashes(tmp_path: Path, capsys):
    _init(tmp_path)
    run(tmp_path, "add-questions", "PANW", "--section", "valuation",
        "--question", "a?", "--question", "b?")
    a, b = question_hash("valuation", "a?"), question_hash("valuation", "b?")
    capsys.readouterr()
    assert run(tmp_path, "drop-question", "PANW", "--question-hash", a,
               "--question-hash", b) == 0
    assert [r["hash"] for r in out(capsys)] == [a, b]


def test_drop_question_on_an_unknown_hash_exits_1(tmp_path: Path):
    _init(tmp_path)
    assert run(tmp_path, "drop-question", "PANW",
               "--question-hash", "deadbeef00") == 1


# --- shared ----------------------------------------------------------------

@pytest.mark.parametrize("command", [
    ["questions", "PANW"],
    ["add-questions", "PANW", "--section", "valuation", "--question", "q?"],
    ["mark-answered", "PANW", "--question-hash", "x", "--sources", "y"],
    ["record-attempt", "PANW", "--question-hash", "x"],
    ["drop-question", "PANW", "--question-hash", "x"],
])
def test_every_ledger_command_needs_an_initialized_ticker(tmp_path: Path, command):
    assert run(tmp_path, *command) == 1
