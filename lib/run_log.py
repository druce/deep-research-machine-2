#!/usr/bin/env python3
"""The per-run audit log: per-task logs in, one readable Markdown file out
(spec §23.4).

`run_stats.json` records what a run COST. It cannot record what a run DID —
what was fetched, which prompt was sent, what came back — and the two places
that knowledge lives cannot write it down:

- a Workflow script has no filesystem, and §15.2 bans `Date.now()`, so it can
  neither log nor time itself;
- the orchestrating skill sees only an agent's final message.

So each agent writes its own log, one file per agent under `reports/<run>/log/`,
and this module assembles them. One writer per file means no contention and no
interleaving, which is what makes the assembled log readable in phase order
rather than in whatever order the agents happened to finish.

Two rules the module enforces rather than documents:

1. **Tokens are joined, never duplicated.** An agent cannot see its own usage,
   so token counts stay in `run_stats.json` and are matched to task logs on
   `(purpose, section, round)`. There is exactly one writer for any given fact.
2. **Nothing is silently dropped.** A `run_stats` entry with no task log, and a
   task log with no `run_stats` entry, both appear under "Unattributed" — the
   discipline `write_wave.js` already applies to sections that return no draft.

Deterministic: the same inputs always produce byte-identical output, like the
source manifest and the wiki index, so regenerating is never a spurious diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import frontmatter
import yaml

from lib.manifest import cell
from lib.run_stats import (
    PURPOSES, check_budgets, elapsed_minutes, load_run_stats,
)

RUN_LOG_NAME = "run_log.md"
LOG_SUBDIR = "log"
PROMPTS_SUBDIR = "prompts"

# How much of a task log's body survives into the assembled file. A run log
# nobody scrolls to the end of has failed at the one thing it is for, and the
# full text is one click away in every case.
MAX_BODY_LINES = 40
MAX_BODY_CHARS = 3000


@dataclass
class TaskLog:
    """One agent's own account of what it did."""
    path: Path
    name: str
    purpose: str
    section: str | None
    round: int | None
    label: str
    started_at: str
    finished_at: str
    status: str
    body: str
    outputs: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated: bool = False

    @property
    def key(self) -> tuple[str, str | None, int | None]:
        return (self.purpose, self.section, self.round)

    @property
    def minutes(self) -> float | None:
        return _minutes_between(self.started_at, self.finished_at)


# --- reading --------------------------------------------------------------

def log_dir(run_dir: Path) -> Path:
    return run_dir / LOG_SUBDIR


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _minutes_between(start: str, end: str) -> float | None:
    try:
        began = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (ended - began).total_seconds() / 60.0


def read_task_logs(run_dir: Path) -> list[TaskLog]:
    """Every task log in the run, in the order they started.

    A malformed log is read for whatever it does carry rather than skipped: a
    file an agent wrote badly is still evidence that the agent ran, and
    dropping it would understate the run.

    Ordering falls back through `started_at`, then the `NN_` filename prefix,
    then mtime. mtime is used only for ORDERING and never printed, so the
    assembled file stays byte-stable.
    """
    directory = log_dir(run_dir)
    if not directory.is_dir():
        return []

    logs: list[TaskLog] = []
    for path in sorted(directory.glob("*.md")):
        try:
            post = frontmatter.load(path)
            meta, body = dict(post.metadata), post.content
        except (OSError, ValueError, yaml.YAMLError):
            # Agents write these by hand, so malformed YAML is a question of
            # when, not whether. The run log is the artifact you reach for when
            # something has already gone wrong; failing to build because one
            # agent mis-quoted a colon would lose the other twenty accounts.
            meta, body = {}, ""
        outputs = meta.get("outputs") or []
        logs.append(TaskLog(
            path=path,
            name=path.stem,
            purpose=str(meta.get("purpose") or "unknown"),
            section=(str(meta["section"])
                     if meta.get("section") not in (None, "") else None),
            round=_int_or_none(meta.get("round")),
            label=str(meta.get("label") or path.stem),
            started_at=str(meta.get("started_at") or ""),
            finished_at=str(meta.get("finished_at") or ""),
            status=str(meta.get("status") or "ok"),
            body=body,
            outputs=[str(o) for o in outputs] if isinstance(outputs, list) else [],
        ))

    def order(log: TaskLog) -> tuple[int, str, str, float]:
        return (0 if log.started_at else 1, log.started_at, log.name,
                log.path.stat().st_mtime if log.path.exists() else 0.0)

    return sorted(logs, key=order)


def join_tokens(logs: list[TaskLog], stats: dict) -> list[dict]:
    """Attach each `run_stats` entry to its task log and return the leftovers.

    Matched on `(purpose, section, round)` and consumed one-for-one, so two
    answerers on the same section in the same round take one entry each rather
    than both claiming the first.
    """
    buckets: dict[tuple, list[dict]] = {}
    for entry in stats.get("subagents") or []:
        if not isinstance(entry, dict):
            continue
        key = (str(entry.get("purpose")),
               entry.get("section") if entry.get("section") else None,
               _int_or_none(entry.get("round")))
        buckets.setdefault(key, []).append(entry)

    for log in logs:
        pending = buckets.get(log.key)
        if not pending:
            continue
        entry = pending.pop(0)
        log.input_tokens = _int_or_none(entry.get("input_tokens"))
        log.output_tokens = _int_or_none(entry.get("output_tokens"))
        log.estimated = bool(entry.get("estimated"))

    return [entry for pending in buckets.values() for entry in pending]


# --- rendering ------------------------------------------------------------

def truncate(text: str, link: str | None = None, *,
             max_lines: int = MAX_BODY_LINES,
             max_chars: int = MAX_BODY_CHARS) -> str:
    """`text` capped at a line and character budget, with a pointer to the rest.

    The marker names where the full text is rather than just saying it was cut:
    a truncation the reader cannot undo is a truncation that loses the
    evidence.
    """
    lines = text.strip().splitlines()
    cut = len(lines) > max_lines
    kept = lines[:max_lines]
    if sum(len(line) + 1 for line in kept) > max_chars:
        budget, trimmed = 0, []
        for line in kept:
            if budget + len(line) + 1 > max_chars:
                cut = True
                break
            trimmed.append(line)
            budget += len(line) + 1
        kept = trimmed
    out = "\n".join(kept).rstrip()
    if cut:
        where = f" — full text: [{link}]({link})" if link else ""
        out += f"\n\n*[… truncated{where}]*"
    return out


def _tokens(value: int | None) -> str:
    return f"{value:,}" if isinstance(value, int) else "—"


def _minutes(value: float | None) -> str:
    return f"{value:.1f}" if isinstance(value, float) else "—"


def _summary_block(ticker: str, run_dir: Path, stats: dict,
                   logs: list[TaskLog]) -> list[str]:
    totals = stats.get("totals") or {}
    wall = elapsed_minutes(stats)
    lines = [
        f"# {ticker} — run {run_dir.name}",
        "",
        "What this run did, assembled from the per-agent logs under "
        f"`{LOG_SUBDIR}/` and the token counts in `run_stats.json` (§23.4).",
        "",
        "| | |",
        "|---|---|",
        f"| Started | {stats.get('started_at') or '—'} |",
        f"| Finished | {stats.get('finished_at') or '— (run unfinished)'} |",
        f"| Wall clock | {_minutes(wall)} min |",
        f"| Agents recorded | {totals.get('subagents', 0)} |",
        f"| Input tokens | {_tokens(_int_or_none(totals.get('input_tokens')))} |",
        f"| Output tokens | {_tokens(_int_or_none(totals.get('output_tokens')))} |",
        f"| Task logs | {len(logs)} |",
    ]

    degraded = [str(k) for k in (stats.get("degraded_kinds") or [])]
    if degraded:
        lines += ["", f"**Degraded kinds (§11.1):** {', '.join(sorted(degraded))}"]

    violations = check_budgets(stats)
    if violations:
        lines += ["", "**Budget violations (§23.3)**", ""]
        lines += [f"- {v}" for v in violations]
    return lines


def _cost_by_purpose(logs: list[TaskLog], stats: dict) -> list[str]:
    """Which phase to cut — the question `run_stats.json` was built to answer
    and has never been able to present."""
    rows: dict[str, dict[str, float]] = {}
    for entry in stats.get("subagents") or []:
        if not isinstance(entry, dict):
            continue
        row = rows.setdefault(str(entry.get("purpose")),
                              {"agents": 0, "input": 0, "output": 0, "minutes": 0.0})
        row["agents"] += 1
        row["input"] += _int_or_none(entry.get("input_tokens")) or 0
        row["output"] += _int_or_none(entry.get("output_tokens")) or 0

    for log in logs:
        row = rows.setdefault(log.purpose,
                              {"agents": 0, "input": 0, "output": 0, "minutes": 0.0})
        if isinstance(log.minutes, float):
            row["minutes"] += log.minutes

    if not rows:
        return []

    def order(item: tuple[str, dict]) -> tuple[int, str]:
        purpose = item[0]
        rank = PURPOSES.index(purpose) if purpose in PURPOSES else len(PURPOSES)
        return (rank, purpose)

    lines = ["", "## Cost by purpose", "",
             "| Purpose | Agents | Input | Output | Agent-minutes |",
             "|---|---:|---:|---:|---:|"]
    for purpose, row in sorted(rows.items(), key=order):
        minutes = f"{row['minutes']:.1f}" if row["minutes"] else "—"
        lines.append(
            f"| {cell(purpose)} | {int(row['agents'])} | "
            f"{int(row['input']):,} | {int(row['output']):,} | {minutes} |")
    # Summed across agents, not wall clock. A phase's agents run concurrently —
    # the 14-answerer wave logs ~138 agent-minutes and takes ~13 by the clock —
    # and a column read as elapsed time would make the widest phase look like
    # the slowest one, which is the opposite of the truth.
    lines += ["", "Agent-minutes are SUMMED across agents in the phase. Agents in "
              "a wave run concurrently, so this is effort, not elapsed time; the "
              "run's wall clock is in the header."]
    return lines


def _timeline(logs: list[TaskLog]) -> list[str]:
    if not logs:
        return []
    lines = ["", "## Timeline", "",
             "| Started | Task | Purpose | Section | Time | Tokens | Status |",
             "|---|---|---|---|---:|---:|---|"]
    for log in logs:
        href = f"{LOG_SUBDIR}/{log.path.name}"
        total = None
        if log.input_tokens is not None or log.output_tokens is not None:
            total = (log.input_tokens or 0) + (log.output_tokens or 0)
        lines.append(
            f"| {log.started_at or '—'} | [{cell(log.label)}]({href}) | "
            f"{cell(log.purpose)} | {cell(log.section or '—')} | "
            f"{_minutes(log.minutes)} | {_tokens(total)}"
            f"{'*' if log.estimated else ''} | {cell(log.status)} |")
    lines += ["", "`*` token count apportioned by the orchestrator rather than "
              "measured (§23.4)."]
    return lines


def _tasks(logs: list[TaskLog]) -> list[str]:
    if not logs:
        return []
    lines = ["", "## Tasks", ""]
    for log in logs:
        href = f"{LOG_SUBDIR}/{log.path.name}"
        lines.append(f"### [{log.label}]({href})")
        lines.append("")
        facts = [f"`{log.purpose}`"]
        if log.section:
            facts.append(f"section `{log.section}`")
        if log.round is not None:
            facts.append(f"round {log.round}")
        if isinstance(log.minutes, float):
            facts.append(f"{log.minutes:.1f} min")
        total = None
        if log.input_tokens is not None or log.output_tokens is not None:
            total = (log.input_tokens or 0) + (log.output_tokens or 0)
        if total is not None:
            facts.append(f"{total:,} tokens")
        if log.status != "ok":
            facts.append(f"**{log.status}**")
        lines += [" · ".join(facts), ""]
        for output in log.outputs:
            lines.append(f"- output: `{output}`")
        if log.outputs:
            lines.append("")
        if log.body.strip():
            lines += [truncate(log.body, href), ""]
    return lines


def _unattributed(orphans: list[dict], logs: list[TaskLog]) -> list[str]:
    """Cost with no account of itself, and accounts with no cost.

    Both are the same defect seen from two sides — an agent nobody wrote down
    — and a log that quietly omitted them would read as complete coverage.
    """
    unlogged = [log for log in logs if log.input_tokens is None
                and log.output_tokens is None]
    if not orphans and not unlogged:
        return []

    lines = ["", "## Unattributed", ""]
    if orphans:
        lines += [f"{len(orphans)} recorded agent"
                  f"{'s' if len(orphans) != 1 else ''} wrote no task log.", "",
                  "| Purpose | Section | Round | Input | Output |",
                  "|---|---|---:|---:|---:|"]
        for entry in sorted(orphans, key=lambda e: (str(e.get("purpose")),
                                                    str(e.get("section")),
                                                    str(e.get("round")))):
            lines.append(
                f"| {cell(entry.get('purpose'))} | "
                f"{cell(entry.get('section') or '—')} | "
                f"{cell(entry.get('round') if entry.get('round') is not None else '—')} | "
                f"{_tokens(_int_or_none(entry.get('input_tokens')))} | "
                f"{_tokens(_int_or_none(entry.get('output_tokens')))} |")
    if unlogged:
        if orphans:
            lines.append("")
        lines += [f"{len(unlogged)} task log"
                  f"{'s' if len(unlogged) != 1 else ''} matched no entry in "
                  f"`run_stats.json`, so their cost is unknown: "
                  + ", ".join(f"`{log.name}`" for log in unlogged) + "."]
    return lines


def _artifacts(ticker_dir: Path, run_dir: Path) -> list[str]:
    candidates = [
        ("Report", "report.md"), ("PDF", "report.pdf"),
        ("References", "references.md"), ("Verdict", "verdict.json"),
        ("Evaluation", "evaluation.json"), ("Run stats", "run_stats.json"),
        ("Snapshot", "snapshot.json"),
    ]
    links = [f"[{label}]({name})" for label, name in candidates
             if (run_dir / name).exists()]
    links.append("[Wiki index](../../wiki/00_index.md)")
    links.append("[Phase journal](../../wiki/log.md)")
    return ["", "## Artifacts", "", " · ".join(links)]


def build_run_log(ticker_dir: Path, run_dir: Path) -> Path:
    """Assemble `reports/<run>/run_log.md` and return its path.

    Never raises on missing inputs: a run with no task logs and no stats still
    produces a log saying so, because the most likely moment to want this file
    is right after something went wrong.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    stats = load_run_stats(run_dir)
    logs = read_task_logs(run_dir)
    orphans = join_tokens(logs, stats)

    lines = _summary_block(ticker_dir.name, run_dir, stats, logs)
    lines += _cost_by_purpose(logs, stats)
    lines += _timeline(logs)
    lines += _unattributed(orphans, logs)
    lines += _tasks(logs)
    lines += _artifacts(ticker_dir, run_dir)

    if not logs:
        lines += ["", "No task logs were written for this run. Agents record "
                  f"their own under `{LOG_SUBDIR}/` (§23.4); a run without "
                  "them has cost figures but no account of what was done."]

    out = run_dir / RUN_LOG_NAME
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out


# --- writing a task log (used by tests and by the driver) -----------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def task_log_path(run_dir: Path, sequence: int, purpose: str,
                  slug: str) -> Path:
    """Where one agent's log belongs.

    The `NN_` prefix is a tiebreak for ordering, not the ordering itself —
    agents run concurrently and cannot coordinate on a counter, so
    `started_at` is what actually sorts the timeline.
    """
    clean = _SLUG_RE.sub("-", slug.lower()).strip("-") or "task"
    return log_dir(run_dir) / f"{sequence:02d}_{purpose}_{clean}.md"
