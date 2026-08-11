"""Research-loop constants and batching (spec §14 step 2, §23.3, §24).

The cap is a CONCURRENCY WIDTH, not a question limit — that distinction is the
whole point of §14's "nothing refuses a larger open set", so it is tested as
wave scheduling rather than as truncation.
"""
from __future__ import annotations

from lib.research import (
    DEFAULT_ROUNDS,
    MAX_ATTEMPTS,
    MAX_PARALLEL_AGENTS,
    QUESTIONS_PER_BATCH,
    batch_questions,
    waves,
)


def qs(section: str, n: int, start: int = 0) -> list[dict]:
    return [{"hash": f"{section}{i}", "question": f"q{i}", "section": section,
             "status": "open"} for i in range(start, start + n)]


def test_pinned_constants():
    """§24 pins these; a drift changes what a build costs with nothing failing."""
    assert MAX_PARALLEL_AGENTS == 8
    assert QUESTIONS_PER_BATCH == (2, 4)
    assert MAX_ATTEMPTS == 3
    assert DEFAULT_ROUNDS == 3


# --- batching --------------------------------------------------------------

def test_batches_stay_inside_the_band():
    for n in range(2, 30):
        batches = batch_questions(qs("valuation", n))
        assert all(2 <= len(b) <= 4 for b in batches), (n, [len(b) for b in batches])
        assert sum(len(b) for b in batches) == n


def test_a_short_tail_is_rebalanced_rather_than_left_undersized():
    """5 would chunk 4+1 naively; 1 is below the minimum."""
    assert [len(b) for b in batch_questions(qs("valuation", 5))] == [3, 2]
    assert [len(b) for b in batch_questions(qs("valuation", 9))] == [4, 3, 2]


def test_questions_are_grouped_by_section():
    """A batch goes to ONE researcher, and one section's questions share
    evidence and guidance — mixing sections makes every batch pay for context
    it only half needs."""
    mixed = qs("valuation", 4) + qs("risk_news", 4)
    for batch in batch_questions(mixed):
        assert len({q["section"] for q in batch}) == 1


def test_a_section_below_the_minimum_still_gets_dispatched():
    """Holding a lone question back for a fuller batch later means it is never
    dispatched at all."""
    batches = batch_questions(qs("valuation", 1))
    assert [len(b) for b in batches] == [1]


def test_no_question_is_lost_or_duplicated():
    mixed = qs("valuation", 7) + qs("risk_news", 5) + qs("profile", 3)
    flat = [q["hash"] for b in batch_questions(mixed) for q in b]
    assert sorted(flat) == sorted(q["hash"] for q in mixed)


def test_empty_input_produces_no_batches():
    assert batch_questions([]) == []


# --- wave scheduling -------------------------------------------------------

def test_the_cap_is_a_width_not_a_limit():
    """§24: an open set needing more than MAX_PARALLEL_AGENTS batches runs in
    successive waves. Nothing is dropped."""
    batches = batch_questions(qs("valuation", 33))
    assert len(batches) >= 9                       # §24's worked example
    scheduled = waves(batches)
    assert len(scheduled) == 2                     # 9 batches -> 8 + 1
    assert all(len(w) <= MAX_PARALLEL_AGENTS for w in scheduled)
    assert sum(len(w) for w in scheduled) == len(batches)


def test_every_question_survives_wave_scheduling():
    batches = batch_questions(qs("valuation", 33))
    flat = [q["hash"] for w in waves(batches) for b in w for q in b]
    assert len(flat) == 33
