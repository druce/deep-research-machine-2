#!/usr/bin/env python3
"""Retrieval evaluation over the question ledger (spec §9.2, §20).

Answered research questions are free retrieval test cases: the ledger already
records `(question, accepted bronze evidence)`. This module replays the
question through the same deterministic grep path a researcher uses and reports
`recall@k` — how much of the accepted evidence that path would have surfaced.

What it is and is not, from §9.2 directly:

- **It is a regression test.** The gold set is what prior retrieval surfaced,
  so it is selection-biased by construction and cannot measure absolute
  retrieval quality. It CAN tell you that a change to ranking or tokenization
  made retrieval worse, which is the whole reason it exists.
- **The gold set excludes what grep structurally cannot return**: structured
  artifacts (grep searches documents) and archived sources (grep searches
  current evidence by default, §5). Counting either as a miss would report a
  regression that never happened.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lib.grep import grep
from lib.provenance import resolve_source
from lib.questions import load_questions

DEFAULT_K = 10

# §9.2: "CI fails if mean recall drops by more than 0.02 from the recorded
# baseline." A tolerance rather than an exact match, because grep ranking has
# ties that a corpus refresh can reorder.
BASELINE_TOLERANCE = 0.02

# §9.2's ship gate, for callers that want to check it.
SHIP_GATE = 0.70

# The small inline list §9.2 calls for. A linguistics dependency here would
# make a regression test depend on a corpus download, and the words below are
# the ones that actually appear in research questions.
STOPWORDS: frozenset[str] = frozenset({
    "a", "about", "after", "against", "all", "also", "an", "and", "any", "are",
    "as", "at", "be", "been", "being", "between", "both", "but", "by", "can",
    "did", "do", "does", "doing", "during", "each", "for", "from", "had", "has",
    "have", "how", "if", "in", "into", "is", "it", "its", "many", "may", "might",
    "more", "most", "much", "no", "not", "of", "on", "or", "other", "our", "over",
    "should", "since", "so", "some", "such", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "those", "through", "to",
    "under", "until", "up", "was", "were", "what", "when", "where", "which",
    "while", "who", "why", "will", "with", "would",
})

# Two characters or fewer matches almost every document; a term that matches
# everything makes every question look retrievable.
MIN_TERM_LENGTH = 3

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9&/.-]*")


def query_terms(question: str) -> list[str]:
    """Search terms for a question: lowercased, destopworded, deduped (§9.2).

    Order is first appearance, so the derived query is reproducible — the
    metric is only a regression test if the same ledger produces the same
    query on every machine.
    """
    terms: list[str] = []
    for token in _TOKEN_RE.findall(question.lower()):
        token = token.strip(".-/&")
        if len(token) < MIN_TERM_LENGTH or token in STOPWORDS or token in terms:
            continue
        terms.append(token)
    return terms


def gold_ids(ticker_dir: Path, entry: dict) -> list[str]:
    """The question's accepted evidence, restricted to what grep could return.

    A stamped id survives only if it resolves to a CURRENT document under
    `sources/` — `resolve_source` also finds archived copies, so the archive
    check is an explicit path comparison rather than a resolution failure.
    """
    out: list[str] = []
    for stamp in entry.get("answer_source_ids") or []:
        source_id = stamp.get("id") if isinstance(stamp, dict) else stamp
        if not source_id:
            continue
        path = resolve_source(ticker_dir, str(source_id))
        if path is None or path.parent != ticker_dir / "sources":
            continue                       # structured, derived, or archived
        if source_id not in out:
            out.append(str(source_id))
    return out


def evaluate(ticker_dir: Path, k: int = DEFAULT_K) -> dict:
    """`recall@k` per answered question plus the mean (§9.2).

    A question is SKIPPED, not scored zero, when it has no document gold set or
    no usable query terms: there was nothing grep could have returned, and a
    zero would report a retrieval failure that is really a ledger fact.

    `mean_recall` is `None` when nothing was scoreable — an honest "no
    measurement", which `compare_to_baseline` refuses to pass.
    """
    per_question: list[dict] = []
    skipped = 0

    for entry in load_questions(ticker_dir):
        if entry.get("status") != "answered":
            continue
        gold = gold_ids(ticker_dir, entry)
        terms = query_terms(str(entry.get("question") or ""))
        if not gold or not terms:
            skipped += 1
            continue

        hits = grep(ticker_dir, " ".join(terms), top_k=k)
        returned = [hit.source_id for hit in hits]
        found = [source_id for source_id in gold if source_id in returned]
        per_question.append({
            "hash": entry.get("hash"),
            "section": entry.get("section"),
            "question": entry.get("question"),
            "terms": terms,
            "gold": gold,
            "returned": returned,
            "hit": found,
            "recall": len(found) / len(gold),
        })

    mean = (sum(q["recall"] for q in per_question) / len(per_question)
            if per_question else None)
    return {
        "ticker": ticker_dir.name,
        "k": k,
        "scored": len(per_question),
        "skipped": skipped,
        "mean_recall": mean,
        "per_question": per_question,
    }


def compare_to_baseline(result: dict, baseline_path: Path,
                        tolerance: float = BASELINE_TOLERANCE) -> tuple[bool, str]:
    """`(passed, message)` against a recorded baseline (§9.2).

    Three things fail: a mean below the baseline by more than `tolerance`, a
    baseline file that cannot be read, and a run with no scoreable question.
    The last one matters — "no measurement" reported as "no regression" is how
    a broken ledger passes CI forever.
    """
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"cannot read baseline {baseline_path}: {exc}"

    if isinstance(baseline, dict) and baseline.get("mean_recall") is None:
        # The checked-in file is a placeholder until a real corpus is measured.
        # Refusing is right: a placeholder that passed would be a gate nobody
        # notices is switched off.
        return False, (f"baseline {baseline_path} has no recorded mean_recall "
                       f"yet — record one from a real build before gating on it")
    try:
        recorded = float(baseline["mean_recall"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"cannot read baseline {baseline_path}: {exc}"

    mean = result.get("mean_recall")
    if mean is None:
        return False, (f"no scoreable questions: the ledger has no answered "
                       f"question with document evidence, so recall cannot be "
                       f"compared to the recorded {recorded:.3f}")

    drop = recorded - mean
    if drop > tolerance:
        return False, (f"mean recall {mean:.3f} is {drop:.3f} below the "
                       f"baseline {recorded:.3f} (tolerance {tolerance})")
    return True, (f"mean recall {mean:.3f} vs baseline {recorded:.3f} "
                  f"(tolerance {tolerance})")
