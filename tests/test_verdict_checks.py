"""The verdict card may not contradict the valuation section in silence."""

from __future__ import annotations

import json
from pathlib import Path

from lib.validate import validate
from lib.verdict_checks import check_verdict

RECONCILIATION = (
    "The ten-year DCF at a 12% WACC produces the $38.13 fair value that governs "
    "this rating. The $129.81 probability-weighted scenario frame is shown as a "
    "cross-check and is rejected because its 28x 2028 exit multiple assumes the "
    "AI cloud contracts renew, and both counterparties may cancel on ninety "
    "days notice, so the multiple prices a backlog that does not yet exist."
)

VALUATION_MD = f"## 6. Valuation\n\n{RECONCILIATION}\n"


def _verdict(**overrides) -> dict:
    base = {
        "rating": "Sell",
        "fair_value": 38.13,
        "scenario_weighted_value": 129.81,
        "scenario_weighted_method": "probability-weighted 2028 EV/EBITDA",
        "scenario_probabilities": {"bear": 0.25, "base": 0.50, "bull": 0.25},
        "reconciliation": RECONCILIATION,
    }
    base.update(overrides)
    return base


def test_reconciled_divergence_passes() -> None:
    assert check_verdict(_verdict(), VALUATION_MD) == []


def test_unreconciled_divergence_fails() -> None:
    failures = check_verdict(_verdict(reconciliation=""), VALUATION_MD)

    assert len(failures) == 1
    assert "diverge" in failures[0]


def test_thin_reconciliation_fails() -> None:
    thin = "The DCF governs at $38.13. The $129.81 frame is a cross-check."
    failures = check_verdict(_verdict(reconciliation=thin), f"x\n{thin}\n")

    assert any("words" in f for f in failures)


def test_reconciliation_absent_from_the_section_fails() -> None:
    failures = check_verdict(_verdict(), "## 6. Valuation\n\nNothing relevant.\n")

    assert any("valuation section" in f for f in failures)


def test_reconciliation_must_name_both_figures() -> None:
    silent = (
        "The discounted cash flow model governs this rating because it rests on "
        "contracted revenue rather than on an assumed exit multiple, and the "
        "scenario frame is reported only as a cross-check against it for the "
        "reader who prefers a multiple based approach to this business today."
    )
    failures = check_verdict(_verdict(reconciliation=silent), f"x\n{silent}\n")

    assert any("fair_value" in f for f in failures)
    assert any("scenario_weighted_value" in f for f in failures)


def test_probabilities_must_sum_to_one() -> None:
    failures = check_verdict(
        _verdict(scenario_probabilities={"bear": 0.25, "base": 0.50, "bull": 0.10}),
        VALUATION_MD)

    assert any("sum to" in f for f in failures)


def test_small_divergence_needs_no_reconciliation() -> None:
    assert check_verdict(
        _verdict(scenario_weighted_value=40.0, reconciliation=""), VALUATION_MD) == []


def test_absent_scenario_frame_passes() -> None:
    assert check_verdict({"fair_value": 38.13, "scenario_weighted_value": None},
                         "") == []


def test_a_card_with_no_scenario_fields_at_all_passes() -> None:
    assert check_verdict({"rating": "Buy", "fair_value": 100.0}, "") == []


# --- the gate ------------------------------------------------------------

def test_unreconciled_verdict_fails_the_gate(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run = data_root / "TEST" / "reports" / "2026-08-12"
    (run / "sections").mkdir(parents=True)
    (run / "verdict.json").write_text(json.dumps({
        "rating": "Sell", "fair_value": 38.13,
        "scenario_weighted_value": 129.81, "reconciliation": "",
    }), encoding="utf-8")
    (run / "sections" / "valuation.md").write_text("## 6. Valuation\n",
                                                   encoding="utf-8")

    findings = [f for f in validate(data_root / "TEST", data_root)
                if f.code == "verdict-unreconciled"]

    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_run_without_a_verdict_is_not_a_finding(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "TEST" / "reports" / "2026-08-12").mkdir(parents=True)

    assert [f for f in validate(data_root / "TEST", data_root)
            if f.code == "verdict-unreconciled"] == []


# --- omission is not an escape hatch -------------------------------------

def test_a_weighted_value_in_prose_with_no_field_fails() -> None:
    """Without this, a writer evades every check above by leaving the field out.

    The SPCX card did exactly that: fair_value 38.13, no scenario fields, and a
    valuation section carrying a $129.81 probability-weighted scenario.
    """
    failures = check_verdict(
        {"fair_value": 38.13},
        "## 6. Valuation\n\nThe probability-weighted scenario gives $129.81.\n")

    assert len(failures) == 1
    assert "scenario_weighted_value" in failures[0]


def test_scenario_weighted_wording_is_caught_too() -> None:
    failures = check_verdict(
        {"fair_value": 38.13},
        "A scenario-weighted value of $129.81 falls out of the frame.\n")

    assert len(failures) == 1


def test_a_section_with_no_weighted_value_still_passes() -> None:
    assert check_verdict(
        {"fair_value": 38.13},
        "## 6. Valuation\n\nA ten-year DCF at a 12% WACC gives $38.13.\n") == []


def test_filling_the_field_moves_to_the_divergence_check() -> None:
    failures = check_verdict(
        _verdict(reconciliation=""),
        "## 6. Valuation\n\nThe probability-weighted scenario gives $129.81.\n")

    assert len(failures) == 1
    assert "diverge" in failures[0]


# --------------------------------------------------------------------------
# Thesis pillars — the one-minute read on the front page (§18.2).
# --------------------------------------------------------------------------

SUPPORT = (
    "Adjusted EBITDA excludes all depreciation, and the satellites are rebought "
    "on a five-year cycle. Charging that depreciation costs $2.75 billion a "
    "year against a segment that reported $3.5 billion. The gap is the whole "
    "distance between the reported margin and the cash one."
)


def _pillar(**overrides) -> dict:
    base = {
        "claim": "Adjusted EBITDA excludes $2.75 billion a year of satellite "
                 "depreciation.",
        "support": SUPPORT,
    }
    base.update(overrides)
    return base


def _carded(pillars) -> dict:
    """A verdict whose only interesting field is its pillars."""
    return {"fair_value": 38.13, "pillars": pillars}


def test_absent_pillars_pass() -> None:
    """A run assembled before pillars existed still clears the gold gate."""
    assert check_verdict({"fair_value": 38.13}, "## 6. Valuation\n\nNothing.\n") == []


def test_three_well_formed_pillars_pass() -> None:
    assert check_verdict(_carded([_pillar()] * 3), "x") == []


def test_two_pillars_is_too_few() -> None:
    failures = check_verdict(_carded([_pillar()] * 2), "x")

    assert any("2 entries" in f for f in failures)


def test_five_pillars_is_too_many() -> None:
    failures = check_verdict(_carded([_pillar()] * 5), "x")

    assert any("5 entries" in f for f in failures)


def test_a_claim_without_a_number_fails() -> None:
    """The defect this gate exists for: a heading wearing a claim's clothes."""
    pillars = [_pillar(), _pillar(), _pillar(claim="Margins are the key issue.")]
    failures = check_verdict(_carded(pillars), "x")

    assert any("no number" in f and "pillar 3" in f for f in failures)


def test_an_overlong_claim_fails() -> None:
    pillars = [_pillar(), _pillar(),
               _pillar(claim="1 " + "word " * 45)]
    failures = check_verdict(_carded(pillars), "x")

    assert any("40" in f and "pillar 3" in f for f in failures)


def test_support_that_is_one_sentence_fails() -> None:
    pillars = [_pillar(), _pillar(),
               _pillar(support="Depreciation is $2.75 billion a year.")]
    failures = check_verdict(_carded(pillars), "x")

    assert any("1 sentences" in f for f in failures)


def test_support_that_runs_long_fails() -> None:
    pillars = [_pillar(), _pillar(),
               _pillar(support="We hold the view. " * 6)]
    failures = check_verdict(_carded(pillars), "x")

    assert any("6 sentences" in f for f in failures)


def test_decimals_and_abbreviations_do_not_end_sentences() -> None:
    """"$38.13" and "U.S." must not read as sentence boundaries."""
    support = (
        "The U.S. Air Force paid $38.13 million under the contract. "
        "Revenue per launch fell 4.5% against the prior year. "
        "We treat the decline as mix, not price."
    )
    assert check_verdict(_carded([_pillar(support=support)] * 3), "x") == []


def test_pillars_that_are_not_a_list_fail() -> None:
    failures = check_verdict(_carded({"claim": "x"}), "x")

    assert len(failures) == 1
    assert "must be a list" in failures[0]


def test_a_missing_claim_is_named_by_position() -> None:
    failures = check_verdict(
        _carded([_pillar(), _pillar(claim=""), _pillar()]), "x")

    assert any(f == "pillar 2 has no claim" for f in failures)
