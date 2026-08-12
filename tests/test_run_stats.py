"""Per-run instrumentation and budget checks (spec §23.3, §23.4).

The rule these tests defend: the cost record is a fact about the run, kept in
one vocabulary. A `purpose` outside §23.4's list means an agent was spent
somewhere nobody is accounting for, and the budget gate reads the same file the
orchestrator wrote — so what a build reports and what it is measured on cannot
diverge.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.questions import DEFAULT_ORIGIN
from lib.run_stats import (
    PURPOSES, check_budgets, load_run_stats, record_subagent, recompute_totals,
    start_run, finish_run, write_run_stats,
)

START = "2026-08-11T09:00:00+00:00"
END = "2026-08-11T09:45:00+00:00"


def _stats(**overrides) -> dict:
    base = {
        "started_at": START,
        "finished_at": END,
        "degraded_kinds": [],
        "subagents": [
            {"purpose": "answerer", "section": "valuation", "round": 1,
             "input_tokens": 61_200, "output_tokens": 3_100},
            {"purpose": "section-write", "section": "valuation", "round": None,
             "input_tokens": 48_000, "output_tokens": 5_400},
        ],
    }
    base.update(overrides)
    return recompute_totals(base)


# --- the purpose vocabulary -----------------------------------------------

def test_the_vocabulary_is_the_spec_list_plus_the_polish_stages():
    assert {"deep-research", "answerer", "synthesizer", "rater", "lint",
            "section-write", "section-critic", "section-rewrite",
            "chart-select"} <= set(PURPOSES)
    for stage in ("cross-section", "conclusion", "critique", "polish", "evaluate"):
        assert stage in PURPOSES, stage


def test_recording_an_unknown_purpose_is_rejected():
    """An agent spent under a name nobody accounts for is an unbudgeted agent."""
    stats = start_run(START)
    with pytest.raises(ValueError, match="purpose"):
        record_subagent(stats, purpose="freelancing", input_tokens=10,
                        output_tokens=1)


def test_the_purpose_vocabulary_is_shared_with_question_origins():
    """§23.4: the same names, plus `seed` and `user`, are a ledger question's
    `origin`, so a question's provenance and an agent's cost use one set."""
    assert DEFAULT_ORIGIN == "seed"
    assert "seed" not in PURPOSES and "user" not in PURPOSES


# --- recording ------------------------------------------------------------

def test_record_subagent_appends_and_updates_totals():
    stats = start_run(START)
    record_subagent(stats, purpose="answerer", section="valuation", round_=1,
                    input_tokens=61_200, output_tokens=3_100)
    record_subagent(stats, purpose="synthesizer", section="valuation", round_=1,
                    input_tokens=40_000, output_tokens=2_000)
    assert stats["totals"] == {"subagents": 2, "input_tokens": 101_200,
                               "output_tokens": 5_100}


def test_every_recorded_agent_carries_its_own_token_counts():
    """§23.4: "Token counts must be recorded per agent" — a run total alone
    cannot say which phase to cut."""
    stats = start_run(START)
    record_subagent(stats, purpose="rater", input_tokens=12_000, output_tokens=900)
    entry = stats["subagents"][0]
    assert entry["input_tokens"] == 12_000 and entry["output_tokens"] == 900
    assert entry["purpose"] == "rater"


def test_missing_token_counts_are_rejected():
    """A recorded agent with no cost silently understates the budget."""
    stats = start_run(START)
    with pytest.raises(ValueError, match="tokens"):
        record_subagent(stats, purpose="lint", input_tokens=None,
                        output_tokens=100)


def test_finish_run_stamps_the_end_and_recomputes_totals():
    stats = start_run(START)
    record_subagent(stats, purpose="lint", input_tokens=1_000, output_tokens=10)
    finish_run(stats, END)
    assert stats["finished_at"] == END
    assert stats["totals"]["subagents"] == 1


# --- budgets --------------------------------------------------------------

def test_a_run_inside_every_budget_reports_no_violation():
    assert check_budgets(_stats()) == []


def test_too_many_subagents_is_a_violation():
    stats = _stats(subagents=[{"purpose": "answerer", "input_tokens": 1,
                               "output_tokens": 1}] * 81)
    violations = check_budgets(stats)
    assert any("subagents" in v and "81" in v for v in violations)


def test_too_many_tokens_is_a_violation():
    stats = _stats(subagents=[{"purpose": "answerer", "input_tokens": 6_000_000,
                               "output_tokens": 1_000}])
    assert any("tokens" in v for v in check_budgets(stats))


def test_wall_clock_comes_from_the_timestamps():
    """§23.3: the wall-clock gate is checkable from the same record as the
    token gate."""
    stats = _stats(finished_at="2026-08-11T10:30:00+00:00")   # 90 minutes
    violations = check_budgets(stats)
    assert any("minutes" in v and "90" in v for v in violations)


def test_incremental_ceilings_are_expressible():
    """§23.2's directed-research ceilings: 8 agents, 30 minutes."""
    stats = _stats(subagents=[{"purpose": "answerer", "input_tokens": 1,
                               "output_tokens": 1}] * 9)
    assert check_budgets(stats, max_subagents=8, max_minutes=30)
    quick = _stats(finished_at="2026-08-11T09:20:00+00:00")   # 20 minutes, 2 agents
    assert check_budgets(quick, max_subagents=8, max_minutes=30) == []


def test_an_unfinished_run_is_not_a_wall_clock_violation():
    """A build still in flight has no `finished_at`; reporting it as over
    budget would make the check useless while it is most wanted."""
    stats = _stats(finished_at=None)
    assert not any("minutes" in v for v in check_budgets(stats))


def test_unparseable_timestamps_do_not_crash_the_check():
    stats = _stats(started_at="not a time")
    assert not any("minutes" in v for v in check_budgets(stats))


def test_budget_violations_name_the_limit_they_broke():
    stats = _stats(subagents=[{"purpose": "answerer", "input_tokens": 1,
                               "output_tokens": 1}] * 81)
    assert "80" in " ".join(check_budgets(stats))


# --- persistence ----------------------------------------------------------

def test_write_and_load_round_trip(tmp_path: Path):
    run_dir = tmp_path / "reports" / "2026-08-11"
    run_dir.mkdir(parents=True)
    stats = _stats()
    path = write_run_stats(run_dir, stats)
    assert path == run_dir / "run_stats.json"
    assert load_run_stats(run_dir) == stats


def test_load_of_a_missing_file_is_an_empty_run(tmp_path: Path):
    run_dir = tmp_path / "reports" / "2026-08-11"
    run_dir.mkdir(parents=True)
    assert load_run_stats(run_dir)["subagents"] == []


def test_writing_preserves_keys_other_phases_own(tmp_path: Path):
    """`assemble` writes its own block into this file (§15.3); instrumentation
    must merge rather than replace."""
    run_dir = tmp_path / "reports" / "2026-08-11"
    run_dir.mkdir(parents=True)
    (run_dir / "run_stats.json").write_text(
        json.dumps({"assemble": {"citations": 42}}), encoding="utf-8")
    write_run_stats(run_dir, _stats())
    loaded = json.loads((run_dir / "run_stats.json").read_text(encoding="utf-8"))
    assert loaded["assemble"] == {"citations": 42}
    assert loaded["totals"]["subagents"] == 2


def test_degraded_kinds_survive_the_round_trip(tmp_path: Path):
    run_dir = tmp_path / "reports" / "2026-08-11"
    run_dir.mkdir(parents=True)
    write_run_stats(run_dir, _stats(degraded_kinds=["transcript"]))
    assert load_run_stats(run_dir)["degraded_kinds"] == ["transcript"]


def test_an_apportioned_count_is_marked_estimated():
    """A phase that reports one total for several agents still counts against
    the budget, but the record must say which entries were measured."""
    stats = start_run(START)
    record_subagent(stats, purpose="deep-research", input_tokens=50_000,
                    output_tokens=2_000, estimated=True)
    record_subagent(stats, purpose="rater", input_tokens=12_000, output_tokens=900)
    assert stats["subagents"][0]["estimated"] is True
    assert "estimated" not in stats["subagents"][1]
    assert stats["totals"]["input_tokens"] == 62_000
