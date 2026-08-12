"""The prose character cap excludes draft citation machinery.

A draft cites `[^2026-05-21_sec_10q]` (22 characters) where the assembled report
prints `[^12]` (5). Counting the draft form charges the writer for machinery the
reader never sees, and charges it hardest to the best-cited sections — SPCX's
competitive draft spent 27.3% of its budget on citation ids, then compressed the
prose to land 5 characters under the cap.
"""

from __future__ import annotations

from pathlib import Path

from lib.hard_checks import run_checks

CITE = "[^2026-05-21_sec_10q]"          # 21 characters, the real draft form


def test_citation_ids_do_not_count_against_the_cap(tmp_path: Path) -> None:
    prose = "Revenue grew 33.2%."                     # 19 characters
    text = prose + CITE * 5                           # 19 + 105 = 124 raw

    assert len(text) > 100
    assert run_checks(text, ["max_length_prose: 100"], tmp_path) == []


def test_prose_over_the_cap_still_fails(tmp_path: Path) -> None:
    text = ("x" * 150) + CITE

    failures = run_checks(text, ["max_length_prose: 100"], tmp_path)

    assert len(failures) == 1
    assert "150" in failures[0]


def test_the_failure_leads_with_prose_and_labels_the_raw_length(tmp_path: Path) -> None:
    """The writer must not read the raw count as the number to cut toward."""
    text = ("x" * 120) + CITE * 10

    failures = run_checks(text, ["max_length_prose: 100"], tmp_path)

    assert failures[0].startswith("max_length_prose: 120 characters of prose")
    assert "raw length is 330" in failures[0]


def test_a_draft_with_no_citations_behaves_like_max_length(tmp_path: Path) -> None:
    text = "x" * 101

    assert run_checks(text, ["max_length_prose: 100"], tmp_path) != []
    assert run_checks("x" * 100, ["max_length_prose: 100"], tmp_path) == []


def test_a_non_numeric_threshold_is_reported(tmp_path: Path) -> None:
    failures = run_checks("x", ["max_length_prose: lots"], tmp_path)

    assert len(failures) == 1
    assert "not a number" in failures[0]


def test_numeric_citations_are_excluded_too(tmp_path: Path) -> None:
    """Post-assembly form, in case a check runs over a renumbered draft."""
    text = ("x" * 95) + "[^12]" * 4

    assert run_checks(text, ["max_length_prose: 100"], tmp_path) == []
