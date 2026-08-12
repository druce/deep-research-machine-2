"""Immutable report-run snapshots (spec §15.3, §7, §24).

The rule these tests defend: a snapshot is a point a later run can be diffed
against. Once a run is stamped, nothing writes into it again — the next build
gets `<date>_2` — and `reports/latest` always names the most recent stamped run.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import sra
from lib.render.runs import current_run, is_snapshotted, next_run
from lib.statefile import load_state, mark_section_dirty, save_state

TODAY = date(2026, 8, 11)
NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)


def _init(tmp_path: Path) -> Path:
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    return tmp_path / "PANW"


def _finished_run(ticker_dir: Path, today: date = TODAY) -> Path:
    """A run directory with the artifacts `assemble` would have left."""
    run_dir = next_run(ticker_dir, today)
    (run_dir / "sections").mkdir(parents=True)
    (run_dir / "sections" / "profile.md").write_text("## 1. Company Profile\n",
                                                     encoding="utf-8")
    (run_dir / "report.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "references.md").write_text("## References\n", encoding="utf-8")
    (run_dir / "citation_map.json").write_text("{}", encoding="utf-8")
    (run_dir / "verdict.json").write_text(json.dumps({"rating": "Buy"}),
                                          encoding="utf-8")
    return run_dir


@pytest.fixture
def snapshot_cli(monkeypatch):
    """`sra.py snapshot` with a pinned clock."""
    monkeypatch.setattr(sra, "_utcnow", lambda: NOW)
    return sra.main


# --- the stamp ------------------------------------------------------------

def test_snapshot_stamps_the_run_and_points_latest_at_it(tmp_path: Path, snapshot_cli):
    ticker_dir = _init(tmp_path)
    run_dir = _finished_run(ticker_dir)

    assert snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)]) == 0
    assert is_snapshotted(run_dir)
    stamp = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert stamp["run"] == run_dir.name
    assert stamp["snapshotted_at"] == NOW.isoformat()

    latest = ticker_dir / "reports" / "latest"
    assert latest.is_symlink()
    assert latest.resolve() == run_dir.resolve()


def test_latest_is_a_relative_symlink(tmp_path: Path, snapshot_cli):
    """An absolute link would break the moment the tree is copied or mounted
    somewhere else, and every chart reader resolves through it."""
    ticker_dir = _init(tmp_path)
    _finished_run(ticker_dir)
    snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)])
    import os

    assert not os.path.isabs(os.readlink(ticker_dir / "reports" / "latest"))


def test_snapshot_updates_state_and_clears_consumed_dirty_sections(
        tmp_path: Path, snapshot_cli):
    ticker_dir = _init(tmp_path)
    state = load_state(ticker_dir)
    mark_section_dirty(state, "valuation")
    save_state(ticker_dir, state)
    _finished_run(ticker_dir)

    snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)])
    state = load_state(ticker_dir)
    assert state["report"]["last_generated"] == NOW.isoformat()
    assert state["report"]["sections_dirty"] == []


def test_snapshot_appends_a_wiki_log_entry(tmp_path: Path, snapshot_cli):
    ticker_dir = _init(tmp_path)
    run_dir = _finished_run(ticker_dir)
    snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)])
    assert run_dir.name in (ticker_dir / "wiki" / "log.md").read_text(encoding="utf-8")


# --- §24: multiple same-day snapshots -------------------------------------

def test_second_same_day_run_gets_a_suffix_and_latest_follows(
        tmp_path: Path, snapshot_cli):
    ticker_dir = _init(tmp_path)
    first = _finished_run(ticker_dir)
    snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)])

    second = current_run(ticker_dir, TODAY)
    assert second.name == "2026-08-11_2"
    second.mkdir(parents=True)
    (second / "report.md").write_text("# second\n", encoding="utf-8")
    (second / "verdict.json").write_text("{}", encoding="utf-8")
    snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)])

    assert (ticker_dir / "reports" / "latest").resolve() == second.resolve()
    # §24: the diff against the first snapshot must remain possible.
    assert first.exists()
    assert (first / "report.md").read_text(encoding="utf-8") == "# report\n"


def test_snapshotting_an_already_stamped_run_is_refused(tmp_path: Path, snapshot_cli, capsys):
    """Re-stamping would move `latest` backwards onto a run the next build has
    already left behind, and rewrite an immutable record's timestamp."""
    ticker_dir = _init(tmp_path)
    _finished_run(ticker_dir)
    snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)])
    capsys.readouterr()

    assert snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)]) == 1
    assert "already snapshotted" in capsys.readouterr().err


# --- refusals -------------------------------------------------------------

def test_snapshot_without_a_report_is_refused(tmp_path: Path, snapshot_cli, capsys):
    ticker_dir = _init(tmp_path)
    run_dir = next_run(ticker_dir, TODAY)
    run_dir.mkdir(parents=True)
    capsys.readouterr()

    assert snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)]) == 1
    assert "report.md" in capsys.readouterr().err


def test_snapshot_with_no_run_at_all_is_refused(tmp_path: Path, snapshot_cli, capsys):
    _init(tmp_path)
    capsys.readouterr()
    assert snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path)]) == 1
    assert "no report run" in capsys.readouterr().err


def test_named_run_can_be_snapshotted(tmp_path: Path, snapshot_cli, capsys):
    ticker_dir = _init(tmp_path)
    run_dir = _finished_run(ticker_dir)
    capsys.readouterr()
    assert snapshot_cli(["snapshot", "PANW", "--data-root", str(tmp_path),
                         "--run", run_dir.name]) == 0
    assert json.loads(capsys.readouterr().out)["run"] == run_dir.name
