#!/usr/bin/env python3
"""Research-loop constants and question batching (spec §14, §23.3).

These four numbers shape the whole fan-out, so they live in one place rather
than being spelled out at each call site: §24 pins them, and a batch size or
concurrency cap that drifted between the driver and a skill would change how
much a build costs without anything failing.
"""
from __future__ import annotations

# §14 step 2. A CONCURRENCY WIDTH, not a question limit: when the open set needs
# more batches than this, they run as successive waves. Nothing refuses a larger
# open set — the run's budget is what bounds spend (§23.3).
MAX_PARALLEL_AGENTS = 8

# §14 step 2: batches of 2-4 related questions, same section first.
QUESTIONS_PER_BATCH = (2, 4)

# §14.0's floor. `attempts` counts dispatches that returned no citable evidence;
# at this many the question becomes `deferred` — retained and revivable, but no
# longer dispatched — so a genuinely unanswerable question is not re-attempted
# by every future run forever.
MAX_ATTEMPTS = 3

# §14: the default round cap for a build.
DEFAULT_ROUNDS = 3


def batch_questions(
    open_qs: list[dict],
    per_batch: tuple[int, int] = QUESTIONS_PER_BATCH,
) -> list[list[dict]]:
    """Group open questions into batches of 2-4, same-section first (§14 step 2).

    Questions are grouped by section before splitting, because a batch is
    dispatched to ONE researcher and questions from one section share evidence,
    guidance and vocabulary — mixing sections makes every batch pay for context
    it only half needs.

    Chunking at `max_n` alone would leave undersized tails — 5 questions would
    split 4+1 — so a short tail is rebalanced against the chunk before it: 5
    becomes 3+2, both inside the band.

    The one batch allowed to fall below `min_n` is a section that has fewer than
    `min_n` questions in total, and it is still dispatched. A lone leftover
    question is worth answering, and holding it back for a fuller batch later
    would mean it is never dispatched at all.
    """
    min_n, max_n = per_batch
    batches: list[list[dict]] = []
    for section in dict.fromkeys(q.get("section") for q in open_qs):
        rows = [q for q in open_qs if q.get("section") == section]
        batches.extend(_split(rows, min_n, max_n))
    return batches


def _split(rows: list[dict], min_n: int, max_n: int) -> list[list[dict]]:
    """Split one section's questions into chunks inside `[min_n, max_n]`.

    Chunks are taken at `max_n` and then rebalanced: if the tail would be
    shorter than `min_n`, questions are moved back from the previous chunk
    until both sit inside the band. With 5 and a (2, 4) band that turns the
    naive 4+1 into 3+2.
    """
    if not rows:
        return []
    if len(rows) <= max_n:
        return [rows]

    chunks = [rows[i:i + max_n] for i in range(0, len(rows), max_n)]
    tail = chunks[-1]
    while len(tail) < min_n and len(chunks[-2]) > min_n:
        tail.insert(0, chunks[-2].pop())
    return chunks


def waves(batches: list[list[dict]],
          max_parallel: int = MAX_PARALLEL_AGENTS) -> list[list[list[dict]]]:
    """Split batches into successive waves of at most `max_parallel` (§14 step 2).

    Parallel within a wave, sequential across waves. This exists so the cap
    reads as what it is — a width — rather than as a ceiling on how many
    questions a run may consider.
    """
    return [batches[i:i + max_parallel]
            for i in range(0, len(batches), max_parallel)]
