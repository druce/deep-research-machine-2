"""The verdict card and its driver-computed implied return (spec §15.3).

§15.3 is unusually direct about this: "The driver recalculates
`implied_return_pct`. It must not trust the model-provided arithmetic." The
number sits on the report's front-page card next to the fair value a reader can
divide themselves, so a plausible-looking wrong percentage is the most visible
error the pipeline can ship — and percentage arithmetic in prose is exactly
where a model produces one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.render.assemble import (
    VERDICT_FIELDS, check_verdict, implied_return, recompute_verdict,
    verdict_path)

GOOD = {
    "rating": "Buy",
    "conviction": "Medium",
    "fair_value": 250.0,
    "horizon_months": 12,
    "current_price": 205.0,
    "implied_return_pct": 21.95,
    "valuation_method": "14x FY2027 EPS, cross-checked against DCF",
    "thesis": "Platform consolidation is compounding faster than seats are churning.",
    "key_risk": "Seat growth stalls before the platform attach rate compounds.",
    "base_case_probability": 0.55,
    "vs_consensus": "in line with",
}


def write_verdict(run_dir: Path, **overrides) -> Path:
    payload = dict(GOOD)
    payload.update(overrides)
    for key, value in list(payload.items()):
        if value is ...:
            del payload[key]
    run_dir.mkdir(parents=True, exist_ok=True)
    path = verdict_path(run_dir)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- the arithmetic ---------------------------------------------------------

def test_implied_return_is_the_spec_formula():
    """§15.3: (fair_value / current_price - 1) * 100."""
    assert implied_return(250.0, 200.0) == 25.0
    assert implied_return(150.0, 200.0) == -25.0
    assert implied_return(200.0, 200.0) == 0.0


def test_implied_return_refuses_the_undefined_cases():
    assert implied_return(250.0, 0) is None
    assert implied_return(250.0, None) is None
    assert implied_return(None, 200.0) is None
    assert implied_return("not a number", 200.0) is None


# --- the override -----------------------------------------------------------

def test_recompute_overrides_a_wrong_model_value(tmp_path: Path):
    """The whole point: the model's arithmetic is replaced, not checked."""
    write_verdict(tmp_path, fair_value=250.0, current_price=200.0,
                  implied_return_pct=99.9)

    ok, verdict, error = recompute_verdict(tmp_path)
    assert ok and error is None
    assert verdict["implied_return_pct"] == 25.0

    on_disk = json.loads(verdict_path(tmp_path).read_text())
    assert on_disk["implied_return_pct"] == 25.0
    assert on_disk["implied_return_source"] == "driver"


def test_recompute_leaves_the_models_inputs_alone(tmp_path: Path):
    """Only the DERIVED field is the driver's. Fair value and current price are
    the model's judgment and its evidence, and overwriting either would be the
    driver having an opinion it has no basis for."""
    write_verdict(tmp_path, fair_value=250.0, current_price=200.0)
    _, verdict, _ = recompute_verdict(tmp_path)

    assert verdict["fair_value"] == 250.0
    assert verdict["current_price"] == 200.0
    assert verdict["rating"] == "Buy"
    assert verdict["thesis"] == GOOD["thesis"]


def test_recompute_is_idempotent(tmp_path: Path):
    """An assemble re-run after a failure must not produce a different card."""
    write_verdict(tmp_path)
    recompute_verdict(tmp_path)
    first = verdict_path(tmp_path).read_bytes()
    recompute_verdict(tmp_path)
    assert verdict_path(tmp_path).read_bytes() == first


def test_an_uncomputable_return_keeps_the_model_value_and_says_so(tmp_path: Path):
    """Blanking the field would remove information rather than correct it — but
    the reader has to be told which number they are looking at."""
    write_verdict(tmp_path, current_price=None, implied_return_pct=12.0)

    ok, verdict, error = recompute_verdict(tmp_path)
    assert ok and error is None
    assert verdict["implied_return_pct"] == 12.0
    assert "model" in verdict["implied_return_source"]


def test_recompute_reports_a_missing_verdict(tmp_path: Path):
    ok, _, error = recompute_verdict(tmp_path)
    assert not ok and "no verdict" in error


def test_recompute_reports_unreadable_json(tmp_path: Path):
    verdict_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    verdict_path(tmp_path).write_text("{not json", encoding="utf-8")
    ok, _, error = recompute_verdict(tmp_path)
    assert not ok and "cannot read" in error


# --- the required shape -----------------------------------------------------

def test_every_spec_field_is_required():
    """§15.3 fixes the field list. A missing key renders as a blank on the
    front-page card rather than as an error anyone notices."""
    assert set(VERDICT_FIELDS) == set(GOOD)
    for field in VERDICT_FIELDS:
        incomplete = {k: v for k, v in GOOD.items() if k != field}
        assert check_verdict(incomplete) == [f"missing field: {field}"]


def test_a_verdict_with_no_rating_is_not_a_verdict():
    assert check_verdict({**GOOD, "rating": None}) == \
        ["field must not be null: rating"]


def test_unsupported_values_may_be_null():
    """The conclusion prompt tells the writer to use null rather than invent a
    value — so a null fair value is honest, not malformed."""
    assert check_verdict({**GOOD, "fair_value": None,
                          "base_case_probability": None}) == []


def test_a_nonsense_price_is_caught():
    assert "current_price must be positive" in \
        check_verdict({**GOOD, "current_price": 0})[0]


@pytest.mark.parametrize("value", [-0.1, 1.5, 55])
def test_base_case_probability_is_a_probability(value):
    """55 means the writer wrote a percentage into a [0, 1] field — which would
    otherwise render as a 5,500% confidence on the card."""
    problems = check_verdict({**GOOD, "base_case_probability": value})
    assert len(problems) == 1 and "probability" in problems[0]


def test_recompute_refuses_a_malformed_verdict(tmp_path: Path):
    """The card is not repaired past its arithmetic: a verdict missing fields is
    the conclusion stage's defect, and silently filling them would hide it."""
    write_verdict(tmp_path, thesis=...)
    ok, _, error = recompute_verdict(tmp_path)
    assert not ok
    assert "missing field: thesis" in error
    # ...and it did not rewrite the file on its way out.
    assert "implied_return_source" not in \
        json.loads(verdict_path(tmp_path).read_text())
