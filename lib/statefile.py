"""Per-ticker `.state.json`: freshness stamps, derivation stamps, build state.

See sra6-spec.md §7 (state shape and rules), §7.1 (crash consistency), §10.1
(`status` and what makes a data kind stale), and §20 (module contract).

`data{}` holds fetch lifecycle state — what `prefetch` gathers and `status`
ages. `derived{}` holds the same lifecycle state for model-produced silver.
The one deliberate crossover is `data.peers_candidates`: the peer gather runs
as a prefetch kind under a 90-day policy (§11.1) even though its artifacts are
silver, which is why the on-disk check below looks under `derived/` too.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lib.provenance import DERIVED_SUBDIR, resolve_source

STATE_NAME = ".state.json"

# `on_new_filing` has no filing-date signal of its own yet — §7 defers
# consulting the SEC filing index to §28.4 — so it ages on this fallback.
EVENT_POLICY_FALLBACK_DAYS = 7


def _state_path(ticker_dir: Path) -> Path:
    return ticker_dir / STATE_NAME


def init_state(ticker_dir: Path, ticker: str) -> dict:
    """Create `.state.json` with every block §7's example carries.

    Raises `FileExistsError` if state already exists — re-initializing would
    silently discard the whole fetch history. `sra.py init` is idempotent by
    checking for the file first, not by overwriting it.
    """
    path = _state_path(ticker_dir)
    if path.exists():
        raise FileExistsError(path)
    state = {
        "ticker": ticker,
        "company_name": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_build": None,
        "last_update": None,
        "peers_asked_at": None,
        "data": {},
        "derived": {},
        "wiki": {},
        "report": {"last_generated": None, "sections_dirty": []},
    }
    save_state(ticker_dir, state)
    return state


def load_state(ticker_dir: Path) -> dict:
    return json.loads(_state_path(ticker_dir).read_text(encoding="utf-8"))


def save_state(ticker_dir: Path, state: dict) -> None:
    """Write state atomically: temp file in `ticker_dir`, then `os.replace`.

    §7.1 commits state after every fetcher returns, so a crash mid-write is a
    real scenario; the replace is what keeps it from truncating the file and
    losing the whole fetch history. The temp file is unlinked on any failure
    rather than left beside `.state.json`.
    """
    path = _state_path(ticker_dir)
    fd, tmp_name = tempfile.mkstemp(dir=ticker_dir, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def record_fetch(
    state: dict,
    data_kind: str,
    current_id: str | list[str],
    fetched_at: datetime,
    policy: dict,
) -> None:
    """Record a completed fetch under `data[data_kind]` (§7).

    `current_id` accepts a single id or a list; §7 requires the stored value
    to always be a list under `current_ids`, since a data kind may produce one
    or many bronze artifacts and consumers must not have to branch on type.
    A caller's list is copied, never aliased.

    `policy` is merged in as given — `{"policy_days": N}` for a time policy,
    `{"policy": "on_earnings"|"on_new_filing"}` for an event policy.
    """
    ids = [current_id] if isinstance(current_id, str) else list(current_id)
    entry: dict[str, object] = {"current_ids": ids, "fetched_at": fetched_at.isoformat()}
    entry.update(policy)
    state["data"][data_kind] = entry


def record_derived(
    state: dict,
    key: str,
    current_id: str,
    updated_at: datetime,
    derived_from: list[dict],
) -> None:
    """Record a produced silver artifact under `derived[key]` (§7).

    Unlike `data{}`, this uses a singular `current_id`: a derived key names one
    artifact. `derived_from` must be a list of STAMPED references —
    `{"id": ..., "fetched_at": ...}` or `{"id": ..., "generated_at": ...}`.
    The stamp is not decoration: structured bronze ids are overwritten in place
    (only `sources/` is immutable), so the timestamp is the only way
    `invalidate` (§10.2) can tell that an input was refetched. An unstamped or
    bare-string reference is rejected here rather than silently stored, since
    the omission would surface much later as an invalidation that never fires.
    """
    for ref in derived_from:
        if not isinstance(ref, dict) or "id" not in ref:
            raise ValueError(
                f"derived_from reference must be a dict with an 'id' key, got {ref!r}"
            )
        if not (ref.get("fetched_at") or ref.get("generated_at") or ref.get("computed_at")):
            raise ValueError(
                f"derived_from reference {ref['id']!r} is unstamped: §7 requires "
                f"'fetched_at', 'generated_at', or 'computed_at' so invalidate "
                f"can detect a refetch"
            )
    state.setdefault("derived", {})[key] = {
        "current_id": current_id,
        "updated_at": updated_at.isoformat(),
        "derived_from": list(derived_from),
    }


def _artifact_exists(ticker_dir: Path, artifact_id: str) -> bool:
    """Is `artifact_id` still on disk anywhere durable under `ticker_dir`?

    Checks the three homes an id recorded in `data.*.current_ids` can have
    (§4.2, §10.1):

    - a bronze document, via `resolve_source` — which also finds it under
      `sources/archive/`, since a superseded document is still on disk and
      still resolvable by citations; "missing" here means gone, not aged,
    - `structured/<id>.json` — bronze JSON,
    - `derived/**/<id>.json` — silver JSON, reachable from `data{}` only via
      `peers_candidates` (§7), whose gather runs as a prefetch kind.
    """
    if resolve_source(ticker_dir, artifact_id) is not None:
        return True
    if (ticker_dir / "structured" / f"{artifact_id}.json").exists():
        return True
    derived_dir = ticker_dir / DERIVED_SUBDIR
    if (derived_dir / f"{artifact_id}.json").exists():
        return True
    return any(derived_dir.glob(f"*/{artifact_id}.json"))


def stale_kinds(
    state: dict,
    now: datetime,
    last_earnings: date | None = None,
    ticker_dir: Path | None = None,
) -> list[str]:
    """Data kinds needing a refetch (§10.1). A kind is stale when its time
    policy expires, its event policy fires, or any id in `current_ids` is
    missing from disk.

    `last_earnings` is the most recent PAST earnings date (see
    `lib.fetchers.calendar.last_earnings_date`), not the next upcoming one: a
    forward-looking date can only fire in the window before it arrives, and
    once it passes and the calendar refetches to a new future estimate the
    check goes permanently silent, so a stale entry would read as fresh
    forever. "An event landed after my last fetch" stays true until we
    refetch.

    `ticker_dir` enables the missing-artifact check; without it only the
    policy checks run (callers that hold state but no directory, e.g. a
    dry-run over a loaded blob, still get a useful answer).

    Only `data{}` is aged here. Silver under `derived{}` goes stale because
    its bronze inputs changed, which is `invalidate`'s question (§10.2), not
    this one's.
    """
    if now.tzinfo is None:
        # fetched_at stamps are always tz-aware; read a naive `now` as UTC rather
        # than raising "can't subtract offset-naive and offset-aware datetimes".
        now = now.replace(tzinfo=timezone.utc)
    stale: list[str] = []
    for kind, entry in state["data"].items():
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
        if ticker_dir is not None and not all(
            _artifact_exists(ticker_dir, i) for i in entry.get("current_ids", [])
        ):
            stale.append(kind)
            continue  # already stale; a policy check cannot add it twice
        if "policy_days" in entry:
            if now - fetched_at > timedelta(days=entry["policy_days"]):
                stale.append(kind)
        else:  # event-driven policy: on_earnings / on_new_filing
            policy = entry.get("policy")
            if policy == "on_earnings" and last_earnings is not None:
                if fetched_at.date() < last_earnings <= now.date():
                    stale.append(kind)
            elif now - fetched_at > timedelta(days=EVENT_POLICY_FALLBACK_DAYS):
                # on_new_filing has no filing-date signal of its own (§28.4), so it
                # deliberately falls through here instead of consuming an earnings
                # date it has nothing to do with.
                stale.append(kind)
    return stale


def mark_section_dirty(state: dict, section: str) -> None:
    """Append a report section to `report.sections_dirty`, deduped. The list
    accumulates until regeneration consumes it (§7)."""
    dirty = state["report"].setdefault("sections_dirty", [])
    if section not in dirty:
        dirty.append(section)
