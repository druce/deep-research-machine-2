"""Coherence checks between the verdict card and the valuation section.

The SPCX 2026-08-12 report headlined a Sell at a $38.13 fair value beside a
$129.81 probability-weighted scenario that implies Hold. The text acknowledged
the gap and never resolved it, and nothing in the pipeline could tell: the
cross-section worklist compares numbers ACROSS sections, and this pair spans the
verdict card and one section.

Parsing the second figure out of model-written prose is too fragile to gate on,
so the scenario numbers live in `verdict.json` and this module checks them.
"""

from __future__ import annotations

import re

# Two values for one quantity, more than this far apart, are a contradiction the
# reader will notice on the first page and must be told about.
DIVERGENCE_THRESHOLD = 0.15

# Short enough that one honest paragraph clears it; long enough that "the DCF
# governs" does not.
MIN_RECONCILIATION_WORDS = 40


def _number(value: object) -> float | None:
    """A float, or None for anything that is not one — §6.4: a value nobody
    reported is absent, not zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _mentions(text: str, value: float) -> bool:
    """Whether `text` names `value`, ignoring currency, commas and separators.

    Compares digit runs, so "$129.81", "129.81" and "USD 129.81" all match the
    figure they came from.
    """
    return re.sub(r"\D", "", f"{value:.2f}") in re.sub(r"\D", "", text)


def check_verdict(verdict: dict, valuation_md: str) -> list[str]:
    """Every coherence failure, in reading order. Empty means the card passes.

    Failures rather than a boolean: the caller turns them into findings, and a
    writer fixing them wants the list, not a verdict on the verdict.
    """
    failures: list[str] = []

    probabilities = verdict.get("scenario_probabilities")
    if isinstance(probabilities, dict) and probabilities:
        total = sum(_number(v) or 0.0 for v in probabilities.values())
        if abs(total - 1.0) > 0.01:
            failures.append(
                f"scenario_probabilities sum to {total:.2f}, not 1.0 — a base "
                f"case probability with no stated complement tells the reader "
                f"nothing")

    weighted = _number(verdict.get("scenario_weighted_value"))
    if weighted is None:
        return failures

    fair = _number(verdict.get("fair_value"))
    if fair is None or fair == 0:
        failures.append("scenario_weighted_value is set but fair_value is not")
        return failures

    divergence = abs(fair - weighted) / abs(fair)
    if divergence <= DIVERGENCE_THRESHOLD:
        return failures

    text = str(verdict.get("reconciliation") or "").strip()
    if not text:
        failures.append(
            f"fair_value {fair:,.2f} and scenario_weighted_value {weighted:,.2f} "
            f"diverge by {divergence:.0%} with no reconciliation")
        return failures

    words = len(text.split())
    if words < MIN_RECONCILIATION_WORDS:
        failures.append(
            f"reconciliation is {words} words; at least "
            f"{MIN_RECONCILIATION_WORDS} are required to explain a "
            f"{divergence:.0%} divergence")

    for label, value in (("fair_value", fair),
                         ("scenario_weighted_value", weighted)):
        if not _mentions(text, value):
            failures.append(
                f"reconciliation does not name {label} ({value:,.2f}) — a "
                f"reconciliation that omits one of the two numbers reconciles "
                f"nothing")

    if text not in valuation_md:
        failures.append(
            "reconciliation text does not appear in the valuation section — a "
            "reconciliation that lives only in JSON reaches no reader")

    return failures
