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
