"""Tests for the per-ticker mutating-command lock (spec §7.1)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lib.lock import LOCK_BREAK_AGE_HOURS, LOCK_NAME, LockHeldError, TickerLock

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write_foreign_lock(ticker_dir: Path, *, age_hours: float, pid: int = 999999,
                        command: str = "prefetch") -> Path:
    """Plant a lock as if another process had taken it `age_hours` ago."""
    path = ticker_dir / LOCK_NAME
    path.write_text(json.dumps({
        "pid": pid,
        "command": command,
        "acquired_at": (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
    }), encoding="utf-8")
    return path


# --- acquire --------------------------------------------------------------

def test_acquire_writes_pid_and_command(tmp_ticker_dir: Path):
    lock = TickerLock(tmp_ticker_dir, "init")
    lock.acquire()
    holder = json.loads((tmp_ticker_dir / LOCK_NAME).read_text(encoding="utf-8"))
    assert holder["pid"] == os.getpid()
    assert holder["command"] == "init"
    assert datetime.fromisoformat(holder["acquired_at"]).tzinfo is not None


def test_second_acquire_raises_lock_held_error_naming_holder(tmp_ticker_dir: Path):
    _write_foreign_lock(tmp_ticker_dir, age_hours=0.1, pid=4242, command="prefetch")
    with pytest.raises(LockHeldError) as exc:
        TickerLock(tmp_ticker_dir, "init").acquire()
    message = str(exc.value)
    assert "4242" in message
    assert "prefetch" in message


def test_lock_held_error_carries_structured_holder_info(tmp_ticker_dir: Path):
    _write_foreign_lock(tmp_ticker_dir, age_hours=0.1, pid=4242, command="prefetch")
    with pytest.raises(LockHeldError) as exc:
        TickerLock(tmp_ticker_dir, "init").acquire()
    assert exc.value.holder_info["pid"] == 4242
    assert exc.value.holder_info["command"] == "prefetch"


def test_failed_acquire_performs_no_writes(tmp_ticker_dir: Path):
    """A losing process must leave the holder's lock byte-identical — the
    whole point is that it fails immediately without touching anything."""
    path = _write_foreign_lock(tmp_ticker_dir, age_hours=0.1)
    before = path.read_bytes()
    with pytest.raises(LockHeldError):
        TickerLock(tmp_ticker_dir, "init").acquire()
    assert path.read_bytes() == before


def test_acquire_is_not_reentrant_within_one_process(tmp_ticker_dir: Path):
    """Two mutating commands for the same ticker fail even in-process; the
    lock is on the ticker directory, not on the object."""
    TickerLock(tmp_ticker_dir, "init").acquire()
    with pytest.raises(LockHeldError):
        TickerLock(tmp_ticker_dir, "prefetch").acquire()


# --- force ----------------------------------------------------------------

def test_force_does_not_break_a_fresh_lock(tmp_ticker_dir: Path):
    """§7.1: `--force-lock` may break a lock only if it is older than 6h.
    A fresh lock is a live sibling process, and stomping it would let two
    writers race on one ticker tree."""
    path = _write_foreign_lock(tmp_ticker_dir, age_hours=1)
    before = path.read_bytes()
    with pytest.raises(LockHeldError):
        TickerLock(tmp_ticker_dir, "init").acquire(force=True)
    assert path.read_bytes() == before


def test_force_breaks_a_lock_older_than_six_hours(tmp_ticker_dir: Path):
    _write_foreign_lock(tmp_ticker_dir, age_hours=LOCK_BREAK_AGE_HOURS + 1, pid=4242)
    TickerLock(tmp_ticker_dir, "init").acquire(force=True)
    holder = json.loads((tmp_ticker_dir / LOCK_NAME).read_text(encoding="utf-8"))
    assert holder["pid"] == os.getpid()
    assert holder["command"] == "init"


def test_an_old_lock_still_blocks_without_force(tmp_ticker_dir: Path):
    """Age alone never breaks a lock — breaking is always an explicit act."""
    _write_foreign_lock(tmp_ticker_dir, age_hours=LOCK_BREAK_AGE_HOURS + 1)
    with pytest.raises(LockHeldError):
        TickerLock(tmp_ticker_dir, "init").acquire()


def test_force_falls_back_to_mtime_when_acquired_at_is_unreadable(tmp_ticker_dir: Path):
    """A truncated lock (crash mid-write) has no readable `acquired_at`. It
    must not become unbreakable, so age falls back to the file's mtime."""
    path = tmp_ticker_dir / LOCK_NAME
    path.write_text("{ this is not json", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=LOCK_BREAK_AGE_HOURS + 1)).timestamp()
    os.utime(path, (old, old))
    TickerLock(tmp_ticker_dir, "init").acquire(force=True)
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_unreadable_fresh_lock_still_blocks(tmp_ticker_dir: Path):
    path = tmp_ticker_dir / LOCK_NAME
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(LockHeldError):
        TickerLock(tmp_ticker_dir, "init").acquire(force=True)


# --- release --------------------------------------------------------------

def test_release_removes_the_lock_file(tmp_ticker_dir: Path):
    lock = TickerLock(tmp_ticker_dir, "init")
    lock.acquire()
    lock.release()
    assert not (tmp_ticker_dir / LOCK_NAME).exists()


def test_release_without_acquire_is_a_noop(tmp_ticker_dir: Path):
    TickerLock(tmp_ticker_dir, "init").release()
    assert not (tmp_ticker_dir / LOCK_NAME).exists()


def test_release_leaves_a_lock_we_no_longer_hold(tmp_ticker_dir: Path):
    """If our lock was force-broken and retaken by another process, releasing
    must not delete that process's lock — we would be handing a third process
    a tree someone is actively writing."""
    lock = TickerLock(tmp_ticker_dir, "init")
    lock.acquire()
    successor = _write_foreign_lock(tmp_ticker_dir, age_hours=0, pid=4242, command="prefetch")
    lock.release()
    assert successor.exists()
    assert json.loads(successor.read_text(encoding="utf-8"))["pid"] == 4242


# --- context manager ------------------------------------------------------

def test_context_manager_acquires_and_releases(tmp_ticker_dir: Path):
    with TickerLock(tmp_ticker_dir, "init"):
        assert (tmp_ticker_dir / LOCK_NAME).exists()
    assert not (tmp_ticker_dir / LOCK_NAME).exists()


def test_context_manager_releases_on_exception(tmp_ticker_dir: Path):
    """A fetcher raising must not strand the lock — that is exactly the case
    §7.1's 6h break exists to clean up after, and it should be rare."""
    with pytest.raises(RuntimeError):
        with TickerLock(tmp_ticker_dir, "init"):
            raise RuntimeError("fetcher blew up")
    assert not (tmp_ticker_dir / LOCK_NAME).exists()


def test_context_manager_honors_force_from_the_constructor(tmp_ticker_dir: Path):
    _write_foreign_lock(tmp_ticker_dir, age_hours=LOCK_BREAK_AGE_HOURS + 1)
    with TickerLock(tmp_ticker_dir, "init", force=True):
        assert json.loads(
            (tmp_ticker_dir / LOCK_NAME).read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_lock_held_error_is_a_runtime_error(tmp_ticker_dir: Path):
    assert issubclass(LockHeldError, RuntimeError)


def test_break_age_is_six_hours():
    assert LOCK_BREAK_AGE_HOURS == 6
