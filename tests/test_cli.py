"""Tests for the sra.py driver CLI skeleton: init, status, containment, locking."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sra
from lib.lock import LOCK_NAME
from lib.statefile import load_state, record_fetch, save_state

TICKER_SUBDIRS = (
    "sources", "sources/archive", "structured",
    "derived", "derived/answers", "derived/peers",
    "wiki", "wiki/entities", "charts", "charts/candidates",
    "reports", "research",
)


def _plant_fresh_lock(ticker_dir: Path, pid: int = 4242, command: str = "prefetch") -> Path:
    path = ticker_dir / LOCK_NAME
    path.write_text(json.dumps({
        "pid": pid,
        "command": command,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    return path


# --- init -----------------------------------------------------------------

def test_init_creates_the_layer_tree(tmp_path: Path):
    assert sra.main(["init", "PANW", "--data-root", str(tmp_path)]) == 0
    d = tmp_path / "PANW"
    for sub in TICKER_SUBDIRS:
        assert (d / sub).is_dir(), sub


def test_init_writes_state_and_wiki_stubs(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    assert load_state(d)["ticker"] == "PANW"
    assert (d / "wiki" / "00_index.md").is_file()
    assert (d / "wiki" / "log.md").is_file()


def test_init_is_idempotent_and_preserves_state(tmp_path: Path):
    """Re-running init must not clobber the fetch history — §3's phases are
    all idempotent and rerunning one is the sanctioned recovery move."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    state = load_state(d)
    state["company_name"] = "Palo Alto Networks, Inc."
    save_state(d, state)

    assert sra.main(["init", "PANW", "--data-root", str(tmp_path)]) == 0
    assert load_state(d)["company_name"] == "Palo Alto Networks, Inc."


def test_init_normalizes_ticker_case(tmp_path: Path):
    assert sra.main(["init", "panw", "--data-root", str(tmp_path)]) == 0
    assert (tmp_path / "PANW" / ".state.json").is_file()
    assert load_state(tmp_path / "PANW")["ticker"] == "PANW"


def test_init_releases_the_lock(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    assert not (tmp_path / "PANW" / LOCK_NAME).exists()


# --- _MACRO ---------------------------------------------------------------

def test_macro_gets_its_own_smaller_tree(tmp_path: Path):
    """§4: `data/_MACRO/` is sources + structured + state — it has no wiki,
    reports or research of its own; it is shared evidence, not a subject."""
    assert sra.main(["init", "_MACRO", "--data-root", str(tmp_path)]) == 0
    d = tmp_path / "_MACRO"
    for sub in ("sources", "sources/archive", "structured"):
        assert (d / sub).is_dir(), sub
    assert (d / ".state.json").is_file()
    assert not (d / "wiki").exists()
    assert not (d / "reports").exists()


# --- ticker validation and path containment (§8.4 check 7) ----------------

@pytest.mark.parametrize("bad", ["../evil", "A/B", "../../etc", "..", "_EVIL",
                                 "TOOLONGTICKER", "1ABC", "", "A B", "A_B"])
def test_invalid_tickers_exit_1(tmp_path: Path, bad: str):
    assert sra.main(["init", bad, "--data-root", str(tmp_path)]) == 1


def test_rejected_ticker_touches_no_filesystem(tmp_path: Path):
    """§8.4 check 7 rejects BEFORE any filesystem access, so a traversal
    attempt cannot create a directory on the way to being refused."""
    root = tmp_path / "nonexistent-root"
    assert sra.main(["init", "../evil", "--data-root", str(root)]) == 1
    assert not root.exists()


def test_status_validates_the_ticker_too(tmp_path: Path):
    assert sra.main(["status", "../evil", "--data-root", str(tmp_path)]) == 1


@pytest.mark.parametrize("good", ["PANW", "_MACRO", "BRK.B", "RDS-A", "A", "ABCDEFGHIJ"])
def test_valid_tickers_are_accepted(tmp_path: Path, good: str):
    assert sra.main(["init", good, "--data-root", str(tmp_path)]) == 0


def test_ticker_dir_is_under_the_data_root(tmp_path: Path):
    d = sra.ticker_dir(tmp_path, "panw")
    assert d == tmp_path / "PANW"
    assert d.resolve().is_relative_to(tmp_path.resolve())


# --- status ---------------------------------------------------------------

def test_status_uninitialized_exits_1(tmp_path: Path):
    assert sra.main(["status", "MSFT", "--data-root", str(tmp_path)]) == 1


def test_status_round_trip(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    capsys.readouterr()  # drop init's message; each command is its own process in real use
    assert sra.main(["status", "PANW", "--data-root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ticker": "PANW", "stale": [], "sections_dirty": [], "data": {}}


def test_status_reports_stale_and_current_ids(tmp_path: Path, capsys):
    """A recorded fetch whose artifact is not on disk is stale (§10.1), and
    `data` reports the id list §7 stores."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    state = load_state(d)
    record_fetch(state, "prices", "prices_yahoo",
                 datetime.now(timezone.utc), {"policy_days": 1})
    record_fetch(state, "news", "2026-07-30_news_yahoo",
                 datetime.now(timezone.utc), {"policy_days": 5})
    save_state(d, state)
    (d / "sources" / "2026-07-30_news_yahoo.md").write_text("x", encoding="utf-8")
    capsys.readouterr()

    assert sra.main(["status", "PANW", "--data-root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] == ["prices"]
    assert out["data"]["news"]["current_ids"] == ["2026-07-30_news_yahoo"]


def test_status_reports_dirty_sections(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    state = load_state(d)
    state["report"]["sections_dirty"] = ["competitive"]
    save_state(d, state)
    capsys.readouterr()

    sra.main(["status", "PANW", "--data-root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["sections_dirty"] == ["competitive"]


def test_status_ages_a_time_policy(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    d = tmp_path / "PANW"
    state = load_state(d)
    record_fetch(state, "prices", "prices_yahoo",
                 datetime.now(timezone.utc) - timedelta(days=5), {"policy_days": 1})
    save_state(d, state)
    (d / "structured" / "prices_yahoo.json").write_text("{}", encoding="utf-8")
    capsys.readouterr()

    sra.main(["status", "PANW", "--data-root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["stale"] == ["prices"]


# --- locking (§7.1) -------------------------------------------------------

def test_second_mutating_command_fails_via_the_lock(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    _plant_fresh_lock(tmp_path / "PANW")
    assert sra.main(["init", "PANW", "--data-root", str(tmp_path)]) == 1


def test_lock_failure_names_the_holder(tmp_path: Path, capsys):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    _plant_fresh_lock(tmp_path / "PANW", pid=4242, command="prefetch")
    capsys.readouterr()
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    err = capsys.readouterr().err
    assert "4242" in err and "prefetch" in err


def test_a_held_lock_does_not_block_status(tmp_path: Path):
    """Read-only commands do not lock (§7.1) — `status` while a prefetch runs
    is exactly what a user does to watch progress."""
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    _plant_fresh_lock(tmp_path / "PANW")
    assert sra.main(["status", "PANW", "--data-root", str(tmp_path)]) == 0


def test_force_lock_breaks_a_stale_lock(tmp_path: Path):
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    path = _plant_fresh_lock(tmp_path / "PANW")
    path.write_text(json.dumps({
        "pid": 4242, "command": "prefetch",
        "acquired_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
    }), encoding="utf-8")
    assert sra.main(["init", "PANW", "--data-root", str(tmp_path),
                     "--force-lock"]) == 0


def test_a_failed_lock_leaves_the_tree_untouched(tmp_path: Path):
    """The lock is taken before init writes anything, so a losing process
    cannot half-create the tree."""
    _plant_fresh_lock_dir = tmp_path / "MSFT"
    _plant_fresh_lock_dir.mkdir()
    _plant_fresh_lock(_plant_fresh_lock_dir)
    assert sra.main(["init", "MSFT", "--data-root", str(tmp_path)]) == 1
    assert not (_plant_fresh_lock_dir / ".state.json").exists()
    assert not (_plant_fresh_lock_dir / "sources").exists()


# --- parser ---------------------------------------------------------------

def test_data_root_defaults_to_repo_data_dir():
    args = sra.build_parser().parse_args(["status", "PANW"])
    assert args.data_root == sra.DEFAULT_DATA_ROOT


def test_data_root_may_follow_the_subcommand():
    args = sra.build_parser().parse_args(["init", "PANW", "--data-root", "/tmp/x"])
    assert args.data_root == Path("/tmp/x")
