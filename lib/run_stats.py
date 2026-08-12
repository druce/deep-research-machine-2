#!/usr/bin/env python3
"""Per-run instrumentation and budget checks (spec §23.3, §23.4).

`reports/<run>/run_stats.json` is the run's cost record: when it started and
finished, which fetch kinds degraded, and one entry per model subagent with its
own token counts. §23.4 is explicit that "token counts must be recorded per
agent" — a run total alone says a build was expensive without saying which
phase to cut.

Two rules the module enforces rather than documents:

1. **The `purpose` vocabulary is closed.** An agent recorded under a name
   outside §23.4's list is an agent nobody is accounting for, and the budget
   gate would still count it while the phase report would not. Rejecting the
   write is the only place that discrepancy can be caught.
2. **Totals are derived, never asserted.** `recompute_totals` is called on
   every mutation, so the summary a build reports cannot drift from the entries
   it was computed from.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

RUN_STATS_NAME = "run_stats.json"

# §23.4's allowed `purpose` values. The last five are the polish chain's stage
# names, which the spec admits as `<polish-stage-name>`; they are enumerated
# here rather than left open, because "any string is a polish stage" would
# reopen the hole rule 1 exists to close.
PURPOSES: tuple[str, ...] = (
    "deep-research", "answerer", "synthesizer", "rater", "lint",
    "section-write", "section-critic", "section-rewrite", "chart-select",
    "cross-section", "conclusion", "critique", "polish", "evaluate",
)

# §23.3's cold-build ceilings. The token figure is the FAILURE threshold, not
# the goal — §23.3 puts the target at 3M and names the structural levers.
MAX_SUBAGENTS = 100
MAX_TOKENS = 6_000_000
MAX_MINUTES = 60


def start_run(started_at: str) -> dict:
    """A fresh, empty run record stamped with its start time."""
    return {
        "started_at": started_at,
        "finished_at": None,
        "degraded_kinds": [],
        "subagents": [],
        "totals": {"subagents": 0, "input_tokens": 0, "output_tokens": 0},
    }


def _tokens(value: object, field: str) -> int:
    if value is None or isinstance(value, bool):
        raise ValueError(f"record_subagent: {field} is required (§23.4 records "
                         f"token counts per agent)")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"record_subagent: {field} must be a number, "
                         f"got {value!r}") from exc


def record_subagent(stats: dict, *, purpose: str, input_tokens: object,
                    output_tokens: object, section: str | None = None,
                    round_: int | None = None, estimated: bool = False) -> dict:
    """Append one agent's cost and refresh the totals (§23.4).

    Raises `ValueError` for a purpose outside `PURPOSES` or a missing token
    count. Returns the entry it appended.

    `estimated` marks a count the orchestrator derived rather than read — a
    phase that reports one total for five agents, say. The number still counts
    against the budget (it is the best figure available), but the record says
    which entries are measured and which are apportioned, so nobody later reads
    an even split as five identical agents.
    """
    if purpose not in PURPOSES:
        raise ValueError(
            f"record_subagent: unknown purpose {purpose!r} — §23.4 allows "
            f"{', '.join(PURPOSES)}")

    entry = {
        "purpose": purpose,
        "section": section,
        "round": round_,
        "input_tokens": _tokens(input_tokens, "input_tokens"),
        "output_tokens": _tokens(output_tokens, "output_tokens"),
    }
    if estimated:
        entry["estimated"] = True
    stats.setdefault("subagents", []).append(entry)
    recompute_totals(stats)
    return entry


def recompute_totals(stats: dict) -> dict:
    """Rewrite `totals` from `subagents`. Returns the same dict, for chaining.

    A non-numeric token count in an entry contributes 0 rather than raising:
    this runs over records read back off disk, and a malformed entry should
    show up as a suspiciously cheap run in the report, not as a crash in the
    gate that was about to flag it.
    """
    agents = stats.get("subagents") or []
    def total(field: str) -> int:
        out = 0
        for entry in agents:
            value = entry.get(field) if isinstance(entry, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out += int(value)
        return out

    stats["totals"] = {
        "subagents": len(agents),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
    }
    return stats


def finish_run(stats: dict, finished_at: str) -> dict:
    """Stamp the end of the run and refresh the totals."""
    stats["finished_at"] = finished_at
    return recompute_totals(stats)


def elapsed_minutes(stats: dict) -> float | None:
    """Wall clock from the record's own timestamps, or `None` when the run is
    unfinished or the stamps are unreadable."""
    try:
        started = datetime.fromisoformat(str(stats.get("started_at")))
        finished = datetime.fromisoformat(str(stats.get("finished_at")))
    except (TypeError, ValueError):
        return None
    return (finished - started).total_seconds() / 60.0


def check_budgets(run_stats: dict, max_subagents: int = MAX_SUBAGENTS,
                  max_tokens: int = MAX_TOKENS,
                  max_minutes: int = MAX_MINUTES) -> list[str]:
    """Every budget §23.3 sets that this run broke, or an empty list.

    The defaults are the cold-build ceilings; §23.2's incremental flows pass
    their own (8 agents, 30 minutes). Violations are returned rather than
    raised — the orchestrator reports them at the end of a build that has
    already produced a report, and losing the report to a budget message would
    be a worse outcome than the overrun.
    """
    totals = recompute_totals(dict(run_stats))["totals"]
    violations: list[str] = []

    if totals["subagents"] > max_subagents:
        violations.append(
            f"subagents: {totals['subagents']} > {max_subagents} (§23.3)")

    tokens = totals["input_tokens"] + totals["output_tokens"]
    if tokens > max_tokens:
        violations.append(f"tokens: {tokens:,} > {max_tokens:,} (§23.3)")

    minutes = elapsed_minutes(run_stats)
    if minutes is not None and minutes > max_minutes:
        violations.append(f"minutes: {minutes:.0f} > {max_minutes} (§23.3)")
    return violations


# --- persistence ----------------------------------------------------------

def load_run_stats(run_dir: Path) -> dict:
    """The run's record, or an empty one. Total: a missing or malformed file
    reads as a run with nothing recorded, so a status command never fails
    because instrumentation is absent."""
    path = run_dir / RUN_STATS_NAME
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return start_run("")
    if not isinstance(loaded, dict):
        return start_run("")
    loaded.setdefault("subagents", [])
    loaded.setdefault("degraded_kinds", [])
    return loaded


def write_run_stats(run_dir: Path, stats: dict) -> Path:
    """Merge `stats` into `run_stats.json` and return its path.

    Merge, not replace: `assemble` owns its own block in this file (§15.3) and
    runs after the phases that recorded agents.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / RUN_STATS_NAME
    payload: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            payload = existing if isinstance(existing, dict) else {}
        except (OSError, ValueError):
            payload = {}
    payload.update(recompute_totals(stats))

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path
