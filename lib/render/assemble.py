#!/usr/bin/env python3
"""Deterministic assembly of the final report (spec §15.3).

`sra.py assemble` never launches a model agent. Everything here is arithmetic
and file concatenation over what the write wave and the polish chain already
produced.

This module currently holds the verdict pre-flight; Task 12.3 adds the rest of
the assembly path (chartbook validation, citation renumbering, references).

**Why the verdict is recomputed.** §15.3: "The driver recalculates
`implied_return_pct`. It must not trust the model-provided arithmetic." The
number appears on the report's front-page card, a reader will check it against
the fair value beside it, and a model doing percentage arithmetic in prose is
exactly where a plausible-looking wrong number comes from. The two inputs are
the model's judgment; the derived number is the driver's.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

VERDICT_NAME = "verdict.json"

# §15.3's field list, in the order the card reads them.
VERDICT_FIELDS = (
    "rating",
    "conviction",
    "fair_value",
    "horizon_months",
    "current_price",
    "implied_return_pct",
    "valuation_method",
    "thesis",
    "key_risk",
    "base_case_probability",
    "vs_consensus",
)

# Fields that may legitimately be null — the conclusion prompt tells the writer
# to use null rather than invent a value it cannot support. `rating` is not
# among them: a verdict with no call is not a verdict.
REQUIRED_NON_NULL = ("rating",)


def verdict_path(run_dir: Path) -> Path:
    return run_dir / VERDICT_NAME


def _as_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def check_verdict(verdict: dict) -> list[str]:
    """Every problem with a verdict card, or an empty list (§15.3).

    Missing keys rather than wrong values: the card is rendered field by field,
    so an absent key becomes a blank on the front page rather than an error
    anyone notices.
    """
    problems = [f"missing field: {name}" for name in VERDICT_FIELDS
                if name not in verdict]
    problems += [f"field must not be null: {name}" for name in REQUIRED_NON_NULL
                 if name in verdict and verdict[name] in (None, "")]

    price = _as_number(verdict.get("current_price"))
    if price is not None and price <= 0:
        problems.append(f"current_price must be positive (got {price})")

    probability = _as_number(verdict.get("base_case_probability"))
    if probability is not None and not 0.0 <= probability <= 1.0:
        problems.append(
            f"base_case_probability must be a probability in [0, 1] "
            f"(got {probability})")
    return problems


def implied_return(fair_value: object, current_price: object) -> float | None:
    """`(fair_value / current_price - 1) * 100`, or `None` if not computable."""
    fair = _as_number(fair_value)
    price = _as_number(current_price)
    if fair is None or price is None or price == 0:
        return None
    return round((fair / price - 1) * 100, 2)


def recompute_verdict(run_dir: Path) -> tuple[bool, dict, str | None]:
    """Rewrite `verdict.json` with a driver-computed `implied_return_pct`.

    Returns `(success, verdict, error)`. The rewrite is atomic and idempotent:
    running it twice leaves the same bytes, so an assemble that is re-run after
    a failure does not produce a different card.

    A verdict whose fair value or current price is missing keeps whatever the
    model wrote — there is nothing to recompute from, and blanking the field
    would remove information rather than correct it. That case is reported in
    the returned verdict's `implied_return_source`, so the assembler can say
    which number the reader is looking at.
    """
    path = verdict_path(run_dir)
    if not path.exists():
        return False, {}, f"no verdict at {path}"

    try:
        verdict = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, {}, f"cannot read {path}: {exc}"
    if not isinstance(verdict, dict):
        return False, {}, f"{path} is not a JSON object"

    problems = check_verdict(verdict)
    if problems:
        return False, verdict, "; ".join(problems)

    computed = implied_return(verdict.get("fair_value"),
                              verdict.get("current_price"))
    if computed is None:
        verdict["implied_return_source"] = "model (inputs incomplete)"
    else:
        verdict["implied_return_pct"] = computed
        verdict["implied_return_source"] = "driver"

    _write_atomic(path, verdict)
    return True, verdict, None


def _write_atomic(path: Path, payload: dict) -> None:
    """Temp file plus `os.replace` — the same guarantee the ledger gets.

    A crash mid-write here would leave the front-page card half-written, and
    the assembler reads it immediately afterwards.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
