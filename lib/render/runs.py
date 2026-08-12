#!/usr/bin/env python3
"""Report run directories — naming, ordering, and which one is current (§15.3).

A run directory is `reports/<YYYY-MM-DD>`, or `<date>_2`, `<date>_3`… for
later runs on the same day (§15.3's snapshot rule, §24's same-day case).

The invariant this module exists to hold: **a snapshotted run is immutable.**
`snapshot` stamps the run it finalizes; every later phase that needs somewhere
to write asks for `current_run`, which refuses to hand back a stamped
directory and returns the next free name instead. Without that, a second
same-day build would write its sections straight over the first build's
snapshot and §24's "diff against the first snapshot must remain possible"
would be false.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

REPORTS_SUBDIR = "reports"
LATEST_LINK = "latest"
SNAPSHOT_NAME = "snapshot.json"

RUN_NAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?:_(?P<n>\d+))?$")


def _sort_key(name: str) -> tuple[str, int]:
    """Chronological order for run names. The suffix is compared as an integer:
    lexically, `_10` sorts before `_2`, and the newest run would be wrong."""
    match = RUN_NAME_RE.match(name)
    if match is None:
        return (name, 0)
    return (match.group("date"), int(match.group("n") or 1))


def run_dirs(ticker_dir: Path) -> list[Path]:
    """Every run directory, oldest first. Ignores `latest` and anything whose
    name is not a run name, so a stray directory cannot become "the run"."""
    reports = ticker_dir / REPORTS_SUBDIR
    if not reports.is_dir():
        return []
    runs = [p for p in reports.iterdir()
            if p.is_dir() and not p.is_symlink() and RUN_NAME_RE.match(p.name)]
    return sorted(runs, key=lambda p: _sort_key(p.name))


def is_snapshotted(run_dir: Path) -> bool:
    """Has `snapshot` finalized this run? Its contents are then immutable."""
    return (run_dir / SNAPSHOT_NAME).exists()


def next_run(ticker_dir: Path, today: date) -> Path:
    """The first unused run name for `today`: `<date>`, then `<date>_2`, …

    Existence is the test, not the snapshot stamp: a half-written run that was
    never snapshotted still owns its name, and reusing it would interleave two
    builds' sections in one directory.

    The suffix continues from the HIGHEST one in use, not the first gap. A
    deleted `_2` beside a surviving `_3` would otherwise make the newest run
    sort before an older one, and `current_run` reads the newest.
    """
    reports = ticker_dir / REPORTS_SUBDIR
    base = today.isoformat()
    if not (reports / base).exists():
        return reports / base

    taken = [_sort_key(p.name)[1] for p in run_dirs(ticker_dir)
             if _sort_key(p.name)[0] == base]
    return reports / f"{base}_{max(taken, default=1) + 1}"


def current_run(ticker_dir: Path, today: date) -> Path:
    """Where this build's work belongs: the newest run not yet snapshotted,
    else a fresh name for today.

    May not exist yet — the caller creates it. `assemble` and `snapshot` both
    resolve through this function, so they always agree on which run is being
    finished.
    """
    existing = run_dirs(ticker_dir)
    if existing and not is_snapshotted(existing[-1]):
        return existing[-1]
    return next_run(ticker_dir, today)


def resolve_run(ticker_dir: Path, run: str | None, today: date) -> Path:
    """A named run, or `current_run` when the caller did not name one.

    A name is taken literally (including one that does not exist yet), except
    for `latest`, which follows the symlink the snapshot step maintains.
    """
    if run is None:
        return current_run(ticker_dir, today)
    if run == LATEST_LINK:
        link = ticker_dir / REPORTS_SUBDIR / LATEST_LINK
        return link.resolve() if link.exists() else link
    return ticker_dir / REPORTS_SUBDIR / run
