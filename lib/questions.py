#!/usr/bin/env python3
"""The durable question ledger: `data/<T>/research/questions.json` (spec §14).

This is per-ticker state that ACCUMULATES ACROSS RUNS, not per-run scratch
(§14.0). A question survives the build that raised it, so what one run could not
answer is what the next run starts from — and every rule here follows from that:

- **Identity collapses re-proposals.** `sha1(section|question)` means a question
  raised again by a later phase, or a later run, folds into the existing entry.
  Capture is therefore cheap and idempotent, which is what lets §14.0 invite
  EVERY phase (writer, critic, lint, chart selection) to record a gap.
- **`attempts` survives the collapse.** If a re-proposal reset the counter, the
  deferral floor below would never fire for exactly the questions that keep
  coming back.
- **Nothing is refused for volume.** A large open set is a scheduling fact (§14
  step 2 runs it in waves), never an error.
- **Silence never means dropped.** Only a synthesizer drops a question, and only
  explicitly; an unanswerable one becomes `deferred` by counting, not by
  judgment.

`answer_source_ids` holds STAMPED BRONZE ids. Both halves matter: bronze,
because a question answered from `derived/` would terminate in model-mediated
content rather than evidence (§1.2); stamped, because the timestamp is the only
way `invalidate` (§10.2) can tell that a structured artifact was refetched.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lib.provenance import read_source, read_structured, resolve_source
from lib.research import MAX_ATTEMPTS

# §14.1's transition table. `reopened` is a dispatchable state — `invalidate`
# reopens a question precisely so it gets re-answered against the new evidence.
STATUSES = ("open", "answered", "dropped", "deferred", "reopened")

# Statuses the fan-out dispatches (§14 step 2: "Select questions with status
# open. Deferred, answered and dropped questions are not dispatched.").
DISPATCHABLE = ("open", "reopened")

# §14.0: `origin` records who raised the question — the §23.4 purpose vocabulary
# plus `seed` and `user`. Triage information, never evidence, so an unknown value
# is not rejected here; it is recorded as given.
DEFAULT_ORIGIN = "seed"

RESEARCH_SUBDIR = "research"
LEDGER_NAME = "questions.json"


def question_hash(section: str, question: str) -> str:
    """§14's identity: `sha1(f"{section}|{question.strip().lower()}")[:10]`.

    The section is part of the identity on purpose — the same question may
    exist independently in two sections, where it is genuinely two questions
    with two answers.
    """
    key = f"{section}|{question.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def ledger_path(ticker_dir: Path) -> Path:
    """`research/questions.json` — §4's tree has no hidden dot-directory, so
    EXP's `.research/` is renamed here."""
    return ticker_dir / RESEARCH_SUBDIR / LEDGER_NAME


def load_questions(ticker_dir: Path) -> list[dict]:
    """The ledger, or `[]` when it does not exist yet."""
    path = ledger_path(ticker_dir)
    if not path.exists():
        return []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, list) else []


def save_questions(ticker_dir: Path, questions: list[dict]) -> Path:
    """Write the ledger atomically (temp file + `os.replace`).

    Atomic because this file is the ONLY record of accumulated research state:
    a crash mid-write during a long build would otherwise lose every question
    raised so far, and there is nothing to rebuild it from.
    """
    path = ledger_path(ticker_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".questions.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(questions, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def add_questions(
    ticker_dir: Path,
    section: str,
    texts: list[str],
    round_: int = 1,
    origin: str = DEFAULT_ORIGIN,
) -> dict:
    """Add questions to `section`, collapsing repeats. Returns counts (§14.1).

    Every entry in one call takes that call's `section`, `round_` and `origin`.
    A text already present is a NO-OP: its `attempts`, `status` and original
    `origin` are all left alone. Keeping the first `origin` is deliberate —
    it records who first thought the question worth asking, which is the triage
    signal; the re-proposer is not more interesting than the raiser.

    Raises `ValueError` on a hash collision between DIFFERENT `(section,
    question)` pairs, reporting both texts (§14). Never raises for volume.
    """
    questions = load_questions(ticker_dir)
    by_hash = {q["hash"]: q for q in questions}
    added = 0

    for text in texts:
        text = text.strip()
        if not text:
            continue
        qhash = question_hash(section, text)
        existing = by_hash.get(qhash)
        if existing is not None:
            if existing["question"].strip().lower() != text.lower() \
                    or existing["section"] != section:
                raise ValueError(
                    f"question hash collision on {qhash!r}: existing entry "
                    f"({existing['section']}) {existing['question']!r} vs new "
                    f"({section}) {text!r} — refusing to merge two different "
                    f"questions into one ledger entry (§14)")
            continue                      # identical: idempotent no-op
        entry = {
            "hash": qhash,
            "question": text,
            "section": section,
            "status": "open",
            "origin": origin,
            "attempts": 0,
            "round": round_,
            "answer_source_ids": [],
            "answer_artifacts": [],
        }
        questions.append(entry)
        by_hash[qhash] = entry
        added += 1

    save_questions(ticker_dir, questions)
    return {"added": added,
            "open": sum(1 for q in questions if q["status"] in DISPATCHABLE),
            "total": len(questions)}


def open_questions(ticker_dir: Path, section: str | None = None) -> list[dict]:
    """The questions the fan-out may dispatch (§14 step 2)."""
    return [q for q in load_questions(ticker_dir)
            if q.get("status") in DISPATCHABLE
            and (section is None or q.get("section") == section)]


def _find(questions: list[dict], qhash: str) -> dict:
    for q in questions:
        if q.get("hash") == qhash:
            return q
    raise KeyError(f"no question with hash {qhash}")


def record_attempt(ticker_dir: Path, qhash: str) -> str:
    """Count a dispatch that returned no citable evidence; return the new status.

    Flips `open` to `deferred` exactly at `MAX_ATTEMPTS` (§14.0). This is
    deterministic bookkeeping, not judgment — the driver applies it by counting,
    and only a synthesizer may `drop` a question outright (§14.1).

    A question that is no longer dispatchable (already answered or dropped) has
    its counter incremented but its status left alone: a late attempt against a
    closed question must not drag it into the deferral path.
    """
    questions = load_questions(ticker_dir)
    entry = _find(questions, qhash)
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    if entry.get("status") in DISPATCHABLE and entry["attempts"] >= MAX_ATTEMPTS:
        entry["status"] = "deferred"
    save_questions(ticker_dir, questions)
    return entry["status"]


def _bronze_stamp(ticker_dir: Path, artifact_id: str) -> dict:
    """A stamped reference to a BRONZE artifact, or raise `ValueError` (§14.1).

    Bronze is `sources/` (current or archived — a superseded document still
    answers for the citation that named it, §5) or `structured/`. An id that
    resolves only under `derived/` is refused: a question answered from silver
    would record model-mediated content as its evidence, which is the §1.2
    defect this whole layer separation exists to prevent.
    """
    found = resolve_source(ticker_dir, artifact_id)
    if found is not None:
        meta, _ = read_source(found)
        return {"id": artifact_id, "fetched_at": meta.fetched_at}

    structured = ticker_dir / "structured" / f"{artifact_id}.json"
    if structured.exists():
        meta, _ = read_structured(structured)
        stamp = meta.fetched_at or meta.computed_at
        key = "fetched_at" if meta.fetched_at else "computed_at"
        return {"id": artifact_id, key: stamp}

    derived = ticker_dir / "derived"
    silver = (derived / f"{artifact_id}.json").exists() or \
        any(derived.glob(f"*/{artifact_id}.json"))
    if silver:
        raise ValueError(
            f"{artifact_id!r} is silver (derived/): mark-answered accepts bronze "
            f"ids only (§14.1) — a question answered from derived/ would cite "
            f"model-mediated content as evidence")
    raise ValueError(
        f"{artifact_id!r} resolves to no bronze artifact under {ticker_dir}")


def mark_answered(
    ticker_dir: Path,
    qhash: str,
    sources: list[str],
    artifacts: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Close a question against stamped bronze evidence (§14.1).

    Raises `ValueError` when `sources` is empty or names anything that is not
    bronze, and the question is left untouched. That is §14.1's rule made
    structural: "If supporting URL fetches fail and no bronze evidence remains,
    the question stays open" — closing it on no evidence is precisely the
    silent-shortfall this forbids.

    `artifacts` are researcher-answer ids: an AUDIT TRAIL only, never treated as
    evidence (§14). They are not validated as bronze because they are not
    supposed to be.
    """
    if not sources:
        raise ValueError(
            f"mark_answered({qhash!r}) needs at least one bronze source id: a "
            f"question with no citable evidence stays open (§14.1)")

    questions = load_questions(ticker_dir)
    entry = _find(questions, qhash)
    # Every id is resolved BEFORE anything is mutated, so a bad id leaves the
    # ledger exactly as it was rather than half-updated.
    stamps = [_bronze_stamp(ticker_dir, sid) for sid in sources]

    known = {s["id"] for s in entry.get("answer_source_ids", [])}
    entry["answer_source_ids"] = list(entry.get("answer_source_ids", [])) + [
        s for s in stamps if s["id"] not in known]
    if artifacts:
        seen = set(entry.get("answer_artifacts", []))
        entry["answer_artifacts"] = list(entry.get("answer_artifacts", [])) + [
            a for a in artifacts if a not in seen]
    entry["status"] = "answered"
    entry["answered_at"] = (now or datetime.now(timezone.utc)).isoformat()
    save_questions(ticker_dir, questions)
    return entry


def drop_question(ticker_dir: Path, qhash: str) -> dict:
    """Mark a question explicitly out of scope or unanswerable (§14.1).

    Only a synthesizer does this, and only as a decision — the ledger never
    infers `dropped` from silence.
    """
    questions = load_questions(ticker_dir)
    entry = _find(questions, qhash)
    entry["status"] = "dropped"
    save_questions(ticker_dir, questions)
    return entry


def set_status(ticker_dir: Path, qhash: str, status: str) -> dict:
    """Force a status transition (used by `invalidate --apply`, §10.2/§10.3)."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r} (expected one of {STATUSES})")
    questions = load_questions(ticker_dir)
    entry = _find(questions, qhash)
    entry["status"] = status
    save_questions(ticker_dir, questions)
    return entry
