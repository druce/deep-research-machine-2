"""Coherence checks between the verdict card and the valuation section.

The SPCX 2026-08-12 report headlined a Sell at a $38.13 fair value beside a
$129.81 probability-weighted scenario that implies Hold. The text acknowledged
the gap and never resolved it, and nothing in the pipeline could tell: the
cross-section worklist compares numbers ACROSS sections, and this pair spans the
verdict card and one section.

Parsing the second figure out of model-written prose is too fragile to gate on,
so the scenario numbers live in `verdict.json` and this module checks them.

The same file carries the front-matter thesis pillars (§18.2), and they are
gated here for the same reason: a pillar whose claim carries no number is a
heading wearing a claim's clothes, and that is checkable in JSON and not in
prose.
"""

from __future__ import annotations

import re

# Two values for one quantity, more than this far apart, are a contradiction the
# reader will notice on the first page and must be told about.
DIVERGENCE_THRESHOLD = 0.15

# Short enough that one honest paragraph clears it; long enough that "the DCF
# governs" does not.
MIN_RECONCILIATION_WORDS = 40

# The front-matter thesis pillars (§18.2). Three is the fewest that reads as a
# case rather than a headline; four is where a one-minute scan stops being one
# minute.
MIN_PILLARS = 3
MAX_PILLARS = 4

# A claim long enough to need two breaths is not a claim, it is the support.
MAX_CLAIM_WORDS = 40

# Fewer than three sentences and the pillar asserts without evidence; more than
# five and the reader is reading the section instead of scanning the front page.
MIN_SUPPORT_SENTENCES = 3
MAX_SUPPORT_SENTENCES = 5


def _number(value: object) -> float | None:
    """A float, or None for anything that is not one — §6.4: a value nobody
    reported is absent, not zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# A section that talks about a probability-weighted value while the card omits
# `scenario_weighted_value` evades every check below by omission. This detects
# the DISCUSSION, never the figure: extracting "$129.81" from prose is fragile,
# but noticing that the section computes a weighted value is not.
_SCENARIO_PROSE_RE = re.compile(
    r"(?i)\b(probability[- ]weighted|scenario[- ]weighted|"
    r"expected value across (the )?scenarios)\b")


def _mentions(text: str, value: float) -> bool:
    """Whether `text` names `value`, ignoring currency, commas and separators.

    Compares digit runs, so "$129.81", "129.81" and "USD 129.81" all match the
    figure they came from.
    """
    return re.sub(r"\D", "", f"{value:.2f}") in re.sub(r"\D", "", text)


# Periods that end an abbreviation, not a sentence. Without this the counter
# reads "SpaceX sells to the U.S. Air Force." as two sentences and fails a
# pillar that is correctly built.
_ABBREVIATIONS = ("U.S.", "U.K.", "U.N.", "e.g.", "i.e.", "vs.", "Inc.",
                  "Corp.", "Co.", "Ltd.", "No.", "St.", "Mr.", "Ms.", "Mrs.",
                  "Dr.", "Jr.", "Sr.", "Q1.", "Q2.", "Q3.", "Q4.")

# A boundary is terminal punctuation followed by whitespace or the end of the
# string. Requiring the whitespace is what keeps "$38.13" and "4.5%" whole.
_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]*(?:\s+|$)")


def _sentences(text: str) -> int:
    """How many sentences `text` contains, for a range check rather than a parse."""
    stripped = text
    for abbreviation in _ABBREVIATIONS:
        stripped = stripped.replace(abbreviation, abbreviation.replace(".", ""))
    return len(_SENTENCE_END_RE.findall(stripped.strip()))


def _check_pillars(verdict: dict) -> list[str]:
    """Every problem with the thesis pillars, in reading order.

    Absence is not a failure. The conclusion prompt requires pillars, but a
    report assembled before they existed still has to pass the gold gate, and
    the same reasoning governs `scenario_weighted_value` above: gate the shape
    of what is there, never the presence of what an older run could not know to
    write.
    """
    pillars = verdict.get("pillars")
    if pillars is None:
        return []

    if not isinstance(pillars, list):
        return [f"pillars must be a list of objects, got {type(pillars).__name__}"]

    failures: list[str] = []
    if not MIN_PILLARS <= len(pillars) <= MAX_PILLARS:
        failures.append(
            f"pillars has {len(pillars)} entries; the front page carries "
            f"{MIN_PILLARS}-{MAX_PILLARS}")

    for index, pillar in enumerate(pillars, start=1):
        where = f"pillar {index}"
        if not isinstance(pillar, dict):
            failures.append(f"{where} is not an object")
            continue

        claim = str(pillar.get("claim") or "").strip()
        support = str(pillar.get("support") or "").strip()

        if not claim:
            failures.append(f"{where} has no claim")
        else:
            if not any(character.isdigit() for character in claim):
                failures.append(
                    f"{where} claim has no number in it — a claim a reader "
                    f"cannot check is a heading: {claim!r}")
            words = len(claim.split())
            if words > MAX_CLAIM_WORDS:
                failures.append(
                    f"{where} claim runs {words} words; at most "
                    f"{MAX_CLAIM_WORDS} — the rest belongs in its support")

        if not support:
            failures.append(f"{where} has no support")
            continue

        count = _sentences(support)
        if not MIN_SUPPORT_SENTENCES <= count <= MAX_SUPPORT_SENTENCES:
            failures.append(
                f"{where} support is {count} sentences; "
                f"{MIN_SUPPORT_SENTENCES}-{MAX_SUPPORT_SENTENCES} are required")

    return failures


def check_verdict(verdict: dict, valuation_md: str) -> list[str]:
    """Every coherence failure, in reading order. Empty means the card passes.

    Failures rather than a boolean: the caller turns them into findings, and a
    writer fixing them wants the list, not a verdict on the verdict.
    """
    failures: list[str] = _check_pillars(verdict)

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
        match = _SCENARIO_PROSE_RE.search(valuation_md)
        if match is not None:
            failures.append(
                f"the valuation section computes a {match.group(0)} value but "
                f"verdict.json has no scenario_weighted_value — fill it, so the "
                f"two figures can be checked against each other")
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
