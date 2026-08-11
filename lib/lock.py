"""Per-ticker lock for mutating commands (spec §7.1).

Every command that writes into `data/<TICKER>/` takes `data/<TICKER>/.lock`
first, so a second mutating process for the same ticker fails immediately
rather than racing it. Read-only commands (`status`, `manifest`, `grep`,
`show`) do not lock.

There are no multi-file transactions and no rollback (§7.1): recovery is to
rerun the phase, which is safe because every phase is idempotent. The lock's
job is narrower — keep two writers from interleaving in the first place.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCK_NAME = ".lock"

# §7.1: `--force-lock` may break a lock only when it is older than this. A
# fresher lock is presumed to be a live sibling process.
LOCK_BREAK_AGE_HOURS = 6


class LockHeldError(RuntimeError):
    """Raised when the ticker lock is held by someone else.

    Carries `holder_info` — the parsed `.lock` payload (`pid`, `command`,
    `acquired_at`) when it was readable, `{}` when it was not — so a caller
    can report who holds it rather than just that something does.
    """

    def __init__(self, message: str, holder_info: dict | None = None) -> None:
        super().__init__(message)
        self.holder_info: dict = holder_info or {}


class TickerLock:
    """Exclusive lock on one ticker directory, usable as a context manager.

    `force` may be passed to the constructor (for the context-manager form,
    which cannot take arguments at `__enter__`) or to `acquire`; either way
    it only permits breaking a lock older than `LOCK_BREAK_AGE_HOURS`.
    """

    def __init__(self, ticker_dir: Path, command: str, *, force: bool = False) -> None:
        self.ticker_dir = ticker_dir
        self.command = command
        self.path = ticker_dir / LOCK_NAME
        self._force = force
        # The exact payload we wrote, so `release` can tell our own lock from a
        # successor's. None until we actually hold it.
        self._held: dict | None = None

    # --- internals --------------------------------------------------------

    def _read_holder(self) -> dict:
        """Parse the existing lock, returning `{}` if it is unreadable — a
        crash mid-write can leave a truncated file, and that must not become
        an unbreakable lock."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _age(self, holder: dict) -> timedelta | None:
        """Age of the existing lock, or None if it cannot be determined.

        Prefers the recorded `acquired_at`; falls back to the file's mtime so
        a truncated or hand-mangled lock is still breakable after 6h.
        """
        stamp = holder.get("acquired_at")
        if isinstance(stamp, str):
            try:
                acquired = datetime.fromisoformat(stamp)
            except ValueError:
                acquired = None
            if acquired is not None:
                if acquired.tzinfo is None:
                    acquired = acquired.replace(tzinfo=timezone.utc)
                return datetime.now(timezone.utc) - acquired
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return None
        return datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, tz=timezone.utc)

    def _payload(self) -> dict:
        return {
            "pid": os.getpid(),
            "command": self.command,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }

    def _create_exclusive(self) -> bool:
        """Try to create the lock with O_EXCL. Returns False if it already
        exists. The create-or-fail is a single atomic syscall, which is what
        makes this safe between processes — `exists()`-then-write would not
        be."""
        payload = self._payload()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        self._held = payload
        return True

    # --- public API -------------------------------------------------------

    def acquire(self, force: bool = False) -> None:
        """Take the lock, or raise `LockHeldError` without writing anything.

        With `force`, a lock older than `LOCK_BREAK_AGE_HOURS` is removed and
        retaken; a fresher one still raises. Breaking is always explicit —
        age alone never releases a lock, because the holder may simply be a
        long-running fetch.
        """
        if self._create_exclusive():
            return

        holder = self._read_holder()
        if force or self._force:
            age = self._age(holder)
            if age is not None and age > timedelta(hours=LOCK_BREAK_AGE_HOURS):
                # Unlink then re-create with O_EXCL rather than overwriting in
                # place, so two forcing processes still resolve to one winner.
                self.path.unlink(missing_ok=True)
                if self._create_exclusive():
                    return
                holder = self._read_holder()

        raise LockHeldError(
            f"{self.ticker_dir.name} is locked by pid "
            f"{holder.get('pid', 'unknown')} running "
            f"{holder.get('command', 'unknown')!r} since "
            f"{holder.get('acquired_at', 'unknown')} "
            f"(break it with --force-lock once it is over "
            f"{LOCK_BREAK_AGE_HOURS}h old)",
            holder,
        )

    def release(self) -> None:
        """Drop the lock, but only if it is still ours.

        If our lock was force-broken and retaken, the file on disk belongs to
        another process; deleting it would hand a third process a tree
        someone is actively writing. A no-op when we never acquired.
        """
        if self._held is None:
            return
        if self._read_holder() == self._held:
            self.path.unlink(missing_ok=True)
        self._held = None

    def __enter__(self) -> "TickerLock":
        self.acquire(force=self._force)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
