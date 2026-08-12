"""Tests for the per-run audit log (spec §23.4).

The module's whole reason to exist is that neither of the two places that know
what a run did can write it down — a Workflow script has no filesystem, and the
orchestrating skill sees only an agent's final message. So the contract under
test is: agents write their own logs, this assembles them, and nothing an agent
recorded and nothing `run_stats.json` recorded may go missing in between.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sra
from lib.run_log import (
    RUN_LOG_NAME, build_run_log, join_tokens, read_task_logs, task_log_path,
    truncate,
)

STARTED = "2026-08-11T09:00:00+00:00"
FINISHED = "2026-08-11T09:45:00+00:00"


@pytest.fixture
def run_dir(tmp_ticker_dir: Path) -> Path:
    d = tmp_ticker_dir / "reports" / "2026-08-11"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_task_log(run_dir: Path, sequence: int, purpose: str, slug: str, *,
                   section: str | None = None, round_: int | None = 1,
                   started: str = "2026-08-11T09:05:00+00:00",
                   finished: str = "2026-08-11T09:11:00+00:00",
                   status: str = "ok", body: str = "## Notes\n\nDid a thing.",
                   outputs: list[str] | None = None) -> Path:
    path = task_log_path(run_dir, sequence, purpose, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [f"purpose: {purpose}", f"label: {purpose}:{slug}",
            f"started_at: {started}", f"finished_at: {finished}",
            f"status: {status}", f"round: {round_}"]
    if section:
        meta.append(f"section: {section}")
    if outputs:
        meta.append("outputs:")
        meta += [f"  - {o}" for o in outputs]
    path.write_text("---\n" + "\n".join(meta) + "\n---\n\n" + body + "\n",
                    encoding="utf-8")
    return path


def write_stats(run_dir: Path, subagents: list[dict], *,
                started: str = STARTED, finished: str | None = FINISHED) -> None:
    (run_dir / "run_stats.json").write_text(json.dumps({
        "started_at": started, "finished_at": finished,
        "degraded_kinds": [], "subagents": subagents,
        "totals": {
            "subagents": len(subagents),
            "input_tokens": sum(s.get("input_tokens", 0) for s in subagents),
            "output_tokens": sum(s.get("output_tokens", 0) for s in subagents),
        },
    }, indent=2), encoding="utf-8")


def entry(purpose: str, section: str | None = None, round_: int | None = 1,
          input_tokens: int = 1000, output_tokens: int = 100, **extra) -> dict:
    return {"purpose": purpose, "section": section, "round": round_,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            **extra}


# --- reading task logs ----------------------------------------------------

def test_a_task_log_is_read_off_its_frontmatter(run_dir: Path):
    write_task_log(run_dir, 1, "section-write", "valuation",
                   section="valuation", outputs=["sections/valuation.md"])
    log = read_task_logs(run_dir)[0]
    assert log.purpose == "section-write"
    assert log.section == "valuation"
    assert log.round == 1
    assert log.outputs == ["sections/valuation.md"]
    assert "Did a thing." in log.body


def test_logs_are_ordered_by_when_they_started(run_dir: Path):
    """Agents run concurrently and cannot coordinate on a counter, so the
    filename prefix is a tiebreak and `started_at` is the ordering."""
    write_task_log(run_dir, 1, "answerer", "late",
                   started="2026-08-11T09:30:00+00:00")
    write_task_log(run_dir, 2, "answerer", "early",
                   started="2026-08-11T09:05:00+00:00")
    assert [log.label for log in read_task_logs(run_dir)] == [
        "answerer:early", "answerer:late"]


def test_a_log_with_no_start_time_sorts_last_but_is_kept(run_dir: Path):
    """`sra-rater` has no Bash and cannot stamp itself. Dropping its log would
    lose the only account of that agent."""
    write_task_log(run_dir, 1, "rater", "peers", started="", finished="")
    write_task_log(run_dir, 2, "answerer", "moat")
    logs = read_task_logs(run_dir)
    assert len(logs) == 2
    assert logs[-1].purpose == "rater"


def test_a_malformed_log_is_read_for_what_it_carries(run_dir: Path):
    """A file an agent wrote badly is still evidence the agent ran."""
    path = task_log_path(run_dir, 1, "answerer", "broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nnot: [valid\n---\nbody", encoding="utf-8")
    logs = read_task_logs(run_dir)
    assert len(logs) == 1
    assert logs[0].purpose == "unknown"


def test_no_log_directory_is_not_an_error(run_dir: Path):
    assert read_task_logs(run_dir) == []


# --- the token join -------------------------------------------------------

def test_tokens_are_joined_from_run_stats(run_dir: Path):
    """An agent cannot see its own usage, so the counts live in one place and
    are matched in — never written twice."""
    write_task_log(run_dir, 1, "answerer", "moat", section="competitive")
    logs = read_task_logs(run_dir)
    orphans = join_tokens(logs, {"subagents": [
        entry("answerer", "competitive", 1, 61200, 3100)]})
    assert orphans == []
    assert logs[0].input_tokens == 61200
    assert logs[0].output_tokens == 3100


def test_two_agents_on_one_section_take_one_entry_each(run_dir: Path):
    """Entries are consumed one-for-one; both answerers claiming the first
    would double-count it and leave the second looking free."""
    write_task_log(run_dir, 1, "answerer", "moat", section="competitive")
    write_task_log(run_dir, 2, "answerer", "share", section="competitive",
                   started="2026-08-11T09:20:00+00:00")
    logs = read_task_logs(run_dir)
    orphans = join_tokens(logs, {"subagents": [
        entry("answerer", "competitive", 1, 100, 10),
        entry("answerer", "competitive", 1, 200, 20)]})
    assert orphans == []
    assert sorted(log.input_tokens for log in logs) == [100, 200]


def test_an_entry_with_no_task_log_is_returned_as_an_orphan(run_dir: Path):
    logs = read_task_logs(run_dir)
    orphans = join_tokens(logs, {"subagents": [entry("lint")]})
    assert len(orphans) == 1


def test_an_estimated_entry_keeps_its_flag(run_dir: Path):
    write_task_log(run_dir, 1, "section-write", "valuation", section="valuation")
    logs = read_task_logs(run_dir)
    join_tokens(logs, {"subagents": [
        entry("section-write", "valuation", 1, estimated=True)]})
    assert logs[0].estimated is True


# --- truncation -----------------------------------------------------------

def test_truncate_leaves_short_text_alone():
    assert truncate("one\ntwo") == "one\ntwo"


def test_truncate_names_where_the_rest_is():
    """A truncation the reader cannot undo loses the evidence."""
    out = truncate("\n".join(str(n) for n in range(200)), "log/01_x.md")
    assert "truncated" in out
    assert "(log/01_x.md)" in out


def test_truncate_respects_the_character_budget():
    out = truncate("x" * 50 + "\n" + "y" * 50, max_lines=99, max_chars=60)
    assert "truncated" in out
    assert "y" * 50 not in out


# --- the assembled log ----------------------------------------------------

def test_build_writes_the_run_log(tmp_ticker_dir: Path, run_dir: Path):
    write_stats(run_dir, [entry("answerer", "competitive")])
    write_task_log(run_dir, 1, "answerer", "moat", section="competitive")
    path = build_run_log(tmp_ticker_dir, run_dir)
    assert path == run_dir / RUN_LOG_NAME
    text = path.read_text(encoding="utf-8")
    assert "# PANW — run 2026-08-11" in text
    assert "## Timeline" in text
    assert "## Cost by purpose" in text
    assert "(log/01_answerer_moat.md)" in text


def test_the_summary_reports_wall_clock_and_totals(tmp_ticker_dir: Path,
                                                   run_dir: Path):
    write_stats(run_dir, [entry("answerer", "competitive", 1, 61200, 3100)])
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "45.0 min" in text
    assert "61,200" in text


def test_budget_violations_appear_verbatim(tmp_ticker_dir: Path, run_dir: Path):
    write_stats(run_dir, [entry("answerer", "competitive", 1, 7_000_000, 1)])
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "Budget violations" in text
    assert "§23.3" in text


def test_an_unfinished_run_says_so(tmp_ticker_dir: Path, run_dir: Path):
    """The most likely moment to want this file is while something is wrong."""
    write_stats(run_dir, [entry("answerer")], finished=None)
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "run unfinished" in text


def test_cost_by_purpose_follows_the_spec_vocabulary_order(
        tmp_ticker_dir: Path, run_dir: Path):
    write_stats(run_dir, [entry("evaluate"), entry("deep-research"),
                          entry("answerer")])
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert text.index("| deep-research |") < text.index("| answerer |") \
        < text.index("| evaluate |")


def test_recorded_agents_without_a_log_are_surfaced(tmp_ticker_dir: Path,
                                                    run_dir: Path):
    """Cost with no account of itself. A log that omitted these would read as
    complete coverage of a run it had barely described."""
    write_stats(run_dir, [entry("answerer", "competitive"), entry("lint")])
    write_task_log(run_dir, 1, "answerer", "moat", section="competitive")
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "## Unattributed" in text
    assert "1 recorded agent wrote no task log" in text


def test_logs_without_a_cost_entry_are_surfaced_too(tmp_ticker_dir: Path,
                                                    run_dir: Path):
    write_stats(run_dir, [])
    write_task_log(run_dir, 1, "answerer", "moat", section="competitive")
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "matched no entry in `run_stats.json`" in text


def test_a_failed_task_is_marked(tmp_ticker_dir: Path, run_dir: Path):
    write_stats(run_dir, [])
    write_task_log(run_dir, 1, "section-write", "valuation",
                   section="valuation", status="failed")
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "failed" in text


def test_a_run_with_nothing_recorded_still_produces_a_log(
        tmp_ticker_dir: Path, run_dir: Path):
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "No task logs were written" in text


def test_the_run_log_is_deterministic(tmp_ticker_dir: Path, run_dir: Path):
    """Generated, never hand-edited — regenerating must not show up as a
    change, which is also why nothing here is stamped with the current time."""
    write_stats(run_dir, [entry("answerer", "competitive")])
    write_task_log(run_dir, 1, "answerer", "moat", section="competitive")
    first = build_run_log(tmp_ticker_dir, run_dir).read_bytes()
    assert build_run_log(tmp_ticker_dir, run_dir).read_bytes() == first


def test_existing_artifacts_are_linked(tmp_ticker_dir: Path, run_dir: Path):
    write_stats(run_dir, [])
    (run_dir / "report.md").write_text("# report", encoding="utf-8")
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "[Report](report.md)" in text
    assert "[PDF]" not in text          # absent files are not linked


# --- naming ---------------------------------------------------------------

def test_task_log_paths_are_slugged_and_sequenced(tmp_path: Path):
    path = task_log_path(tmp_path, 3, "section-write", "Valuation & Peers")
    assert path.name == "03_section-write_valuation-peers.md"


# --- CLI ------------------------------------------------------------------

def test_run_log_command_writes_the_file(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    run = tmp_path / "PANW" / "reports" / "2026-08-11"
    run.mkdir(parents=True)
    write_stats(run, [entry("answerer", "competitive")])
    assert sra.main(["run-log", "PANW", "--run", "2026-08-11",
                     "--data-root", str(tmp_path)]) == 0
    assert (run / RUN_LOG_NAME).is_file()


def test_run_log_command_reports_a_missing_run(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    assert sra.main(["run-log", "PANW", "--run", "1999-01-01",
                     "--data-root", str(tmp_path)]) == 1


def test_run_log_command_needs_an_initialized_ticker(tmp_path: Path):
    assert sra.main(["run-log", "MSFT", "--data-root", str(tmp_path)]) == 1


def test_phase_time_is_labelled_as_effort_not_elapsed(tmp_ticker_dir: Path,
                                                      run_dir: Path):
    """A phase's agents run concurrently — the 14-answerer wave logs ~138
    agent-minutes and takes ~13 by the clock. A column read as elapsed time
    would make the widest phase look like the slowest, which inverts the
    'which phase do I cut' judgement the table exists for."""
    write_stats(run_dir, [entry("answerer", "competitive"),
                          entry("answerer", "valuation")])
    write_task_log(run_dir, 1, "answerer", "a", section="competitive",
                   started="2026-08-11T09:00:00+00:00",
                   finished="2026-08-11T09:10:00+00:00")
    write_task_log(run_dir, 2, "answerer", "b", section="valuation",
                   started="2026-08-11T09:00:00+00:00",
                   finished="2026-08-11T09:10:00+00:00")
    text = build_run_log(tmp_ticker_dir, run_dir).read_text(encoding="utf-8")
    assert "Agent-minutes" in text
    assert "20.0" in text                      # summed, not the 10 min elapsed
    assert "effort, not elapsed time" in text
