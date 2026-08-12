#!/usr/bin/env python3
"""Deterministic checks a section draft must pass (spec §18.1, §8.2, §22).

These are the mechanical half of quality control: shape, length, and the rule
that internal artifact names never reach report prose. Model judgment — whether
a cited source actually supports the claim, whether a tension is genuine — is
`/sra-lint`'s job and deliberately not here.

Rules come from `sections.yaml`'s `hard_checks` as `"<rule>: <value>"` strings,
and a writer agent runs them over its own draft through this module's CLI:

    uv run python -m lib.hard_checks <file> --rules-json '["startswith: ## 1. …"]'

Two rules carry history worth keeping:

- **Length units are explicit.** `min_length`/`max_length` count CHARACTERS;
  `not_longer_than` counts WORDS. sra5 blurred the two and shipped a shrink gate
  that measured the wrong thing — its polish pass GREW an INTC body by 1,933
  bytes while leaving every flagged redundancy in place, and nothing detected it.
- **An unknown rule fails.** A typo'd rule name that quietly passed would
  disable a gate invisibly, which is worse than having no gate at all.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Draft citation form, `[^2026-05-21_sec_10q]`. Stripped before the prose scan:
# assembly turns these into `[^1]` (§8.2), so flagging them as internal names
# would fail every properly cited section.
CITATION_RE = re.compile(r"\[\^([A-Za-z0-9][\w.\-]*)\]")

# §8.2's prohibition, made precise. Each pattern names something that can only
# be an internal artifact — a JSON filename, a layer directory, a ticker data
# path, the generated manifest.
#
# Deliberately NOT here: the bare word "manifest". §8.2 lists it, but "these
# pressures manifest in gross margin" is ordinary English, and a false positive
# on a build-gating check costs a whole rewrite cycle. The artifact's real name
# is `00_manifest.md`, which the fourth pattern catches.
INTERNAL_NAME_PATTERNS = (
    re.compile(r"\b[\w.-]+\.json\b"),
    re.compile(r"\b(?:structured|derived|sources|wiki|charts)/"),
    re.compile(r"\bdata/[A-Z][A-Z0-9.-]{0,9}\b"),
    re.compile(r"\b00_manifest\b"),
)

LENGTH_RULES = ("min_length", "max_length")


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


def _parse(rule: object) -> tuple[str, str] | None:
    """`(name, value)` for a rule given as a string or a single-key mapping."""
    if isinstance(rule, dict):
        if len(rule) != 1:
            return None
        (name, value), = rule.items()
        return str(name).strip(), str(value)
    if not isinstance(rule, str):
        return None
    name, sep, value = rule.partition(": ")
    if not sep:
        # A valueless rule is legal for the flag-shaped checks.
        stripped = rule.strip()
        return (stripped, "") if stripped in VALUELESS_RULES else None
    return name.strip(), value


def _check_length(name: str, value: str, text: str) -> str | None:
    try:
        threshold = int(value)
    except ValueError:
        return f"{name}: threshold {value!r} is not a number"
    actual = len(text)
    if name == "min_length" and actual < threshold:
        return f"min_length: {actual} characters, needs at least {threshold}"
    if name == "max_length" and actual > threshold:
        return f"max_length: {actual} characters, over the {threshold} cap"
    return None


def _check_not_regex(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        return None
    line = text[:match.start()].count("\n") + 1
    excerpt = match.group(0).strip().splitlines()
    return (f"not_regex: {pattern!r} matched at line {line} "
            f"(starts {excerpt[0][:60]!r}, ends {excerpt[-1][:60]!r})")


def _check_not_longer_than(value: str, text: str, base_dir: Path) -> str | None:
    """Word count against a baseline file — the shrink gate (§18.3).

    A relative path resolves against `base_dir` and may not escape it (§8.4):
    the value is interpolated into a path, and a rule reaching outside the run
    directory is a defect whichever way it was meant.
    """
    candidate = Path(value)
    if candidate.is_absolute():
        other = candidate
    else:
        other = (base_dir / candidate).resolve()
        try:
            other.relative_to(base_dir.resolve())
        except ValueError:
            return (f"not_longer_than: {value!r} resolves outside the base "
                    f"directory")

    if not other.exists():
        # Never a silent pass: a missing baseline means a path substitution
        # went wrong, which is exactly when the gate must not disappear.
        return f"not_longer_than: reference file not found: {value}"

    try:
        theirs = len(other.read_text(encoding="utf-8").split())
    except OSError as exc:
        return f"not_longer_than: cannot read {value}: {exc}"

    mine = len(text.split())
    if mine <= theirs:
        return None
    return (f"not_longer_than: {mine:,} words vs {theirs:,} in {other.name} "
            f"— GREW by {mine - theirs:,}")


def _resolve_inside(value: str, base_dir: Path) -> tuple[Path | None, str | None]:
    """A rule's path argument, resolved under `base_dir` and confined to it.

    Same containment rule as `_check_not_longer_than` (§8.4): the value is
    interpolated into a path, and a rule reaching outside the run directory is a
    defect whichever way it was meant.
    """
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate, None
    resolved = (base_dir / candidate).resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        return None, f"{value!r} resolves outside the base directory"
    return resolved, None


def _split_factor(value: str) -> tuple[str, float] | None:
    """`("<path>", factor)` from `"<path>"` or `"<path> 1.03"`; None if malformed."""
    parts = value.split()
    if len(parts) == 1:
        return parts[0], 1.0
    if len(parts) != 2:
        return None
    try:
        return parts[0], float(parts[1])
    except ValueError:
        return None


def _dir_words(directory: Path) -> int:
    return sum(len(path.read_text(encoding="utf-8").split())
               for path in sorted(directory.glob("*.md")))


def _check_report_not_longer_than(value: str, base_dir: Path,
                                  target: Path | None) -> str | None:
    """Word count of the WHOLE report against a baseline directory (§18.3).

    The per-section gate this supplements made every clarifying word cost a
    deletion from the same section, which is how sentences compress into
    fragments that parse as nothing. The report is the budget; one section may
    grow when another shrinks to pay for it.
    """
    if target is None:
        return ("report_not_longer_than: needs the file under check to locate "
                "the current sections directory; pass target=")
    parsed = _split_factor(value)
    if parsed is None:
        return f"report_not_longer_than: malformed argument {value!r}"
    baseline, factor = parsed

    other, problem = _resolve_inside(baseline, base_dir)
    if problem is not None:
        return f"report_not_longer_than: {problem}"
    if other is None or not other.is_dir():
        # Never a silent pass: a missing baseline means a path substitution went
        # wrong, which is exactly when the gate must not disappear.
        return f"report_not_longer_than: baseline directory not found: {baseline}"

    theirs = _dir_words(other)
    mine = _dir_words(target.parent)
    budget = int(theirs * factor)
    if mine <= budget:
        return None
    return (f"report_not_longer_than: {mine:,} words vs a budget of {budget:,} "
            f"({theirs:,} in {other.name} × {factor:g}) — GREW by "
            f"{mine - budget:,}")


def _check_not_longer_than_pct(value: str, text: str,
                               base_dir: Path) -> str | None:
    """One section against `factor ×` its baseline word count."""
    parsed = _split_factor(value)
    if parsed is None or len(value.split()) != 2:
        return (f"not_longer_than_pct: expected '<baseline file> <factor>', "
                f"got {value!r}")
    baseline, factor = parsed

    other, problem = _resolve_inside(baseline, base_dir)
    if problem is not None:
        return f"not_longer_than_pct: {problem}"
    if other is None or not other.exists():
        return f"not_longer_than_pct: reference file not found: {baseline}"

    theirs = len(other.read_text(encoding="utf-8").split())
    mine = len(text.split())
    budget = int(theirs * factor)
    if mine <= budget:
        return None
    return (f"not_longer_than_pct: {mine:,} words vs a budget of {budget:,} "
            f"({theirs:,} × {factor:g}) — GREW by {mine - budget:,}")


def _check_internal_filenames(text: str) -> str | None:
    """§8.2: internal artifact names must never appear in report prose."""
    prose = CITATION_RE.sub(" ", text)
    found: list[str] = []
    for pattern in INTERNAL_NAME_PATTERNS:
        for match in pattern.finditer(prose):
            token = match.group(0)
            if token not in found:
                found.append(token)
    if not found:
        return None
    return (f"no_internal_filenames: report prose names internal artifacts "
            f"({', '.join(found)}) — the reader has no filesystem")


VALUELESS_RULES = frozenset({"no_internal_filenames"})


def run_checks(text: str, rules: list, base_dir: Path, *,
               target: Path | None = None) -> list[str]:
    """Every failure message, in rule order. An empty list means the draft passes.

    Failures rather than results: a caller wants to know what to fix, and every
    call site here either blocks on the list being empty or feeds it back to a
    rewrite agent as the worklist.
    """
    failures: list[str] = []
    for rule in rules:
        parsed = _parse(rule)
        if parsed is None:
            failures.append(f"malformed rule (expected '<name>: <value>'): {rule!r}")
            continue
        name, value = parsed

        if name in LENGTH_RULES:
            problem = _check_length(name, value, text)
        elif name == "startswith":
            first = _first_line(text)
            problem = (None if first.startswith(value)
                       else f"startswith: first line is {first[:80]!r}, "
                            f"expected it to start {value!r}")
        elif name == "contains":
            problem = None if value in text else f"contains: missing {value!r}"
        elif name == "regex":
            problem = (None if re.search(value, text, re.MULTILINE)
                       else f"regex: {value!r} did not match")
        elif name == "not_regex":
            problem = _check_not_regex(value, text)
        elif name == "not_longer_than":
            problem = _check_not_longer_than(value, text, base_dir)
        elif name == "report_not_longer_than":
            problem = _check_report_not_longer_than(value, base_dir, target)
        elif name == "not_longer_than_pct":
            problem = _check_not_longer_than_pct(value, text, base_dir)
        elif name == "no_internal_filenames":
            problem = _check_internal_filenames(text)
        else:
            problem = f"unknown rule: {name!r}"

        if problem:
            failures.append(problem)
    return failures


def main(argv: list[str] | None = None) -> int:
    """Run the checks over a file and print `{passed, failures}` (§20).

    Exit 1 on any failure, so a writer agent's `&&` chain stops rather than
    reporting success over a draft that did not pass.
    """
    parser = argparse.ArgumentParser(
        description="Run sections.yaml hard checks over a draft file.")
    parser.add_argument("file", help="path to the draft to check")
    parser.add_argument("--rules-json", required=True,
                        help="JSON array of rules, e.g. '[\"max_length: 12000\"]'")
    parser.add_argument("--base-dir", default=None,
                        help="root for relative not_longer_than paths "
                             "(default: the draft's own directory)")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(json.dumps({"passed": False,
                          "failures": [f"file not found: {args.file}"]}))
        return 1

    try:
        rules = json.loads(args.rules_json)
    except ValueError as exc:
        print(json.dumps({"passed": False,
                          "failures": [f"--rules-json is not valid JSON: {exc}"]}))
        return 1
    if not isinstance(rules, list):
        print(json.dumps({"passed": False,
                          "failures": ["--rules-json must be a JSON array"]}))
        return 1

    base_dir = Path(args.base_dir) if args.base_dir else path.parent
    failures = run_checks(path.read_text(encoding="utf-8"), rules, base_dir,
                          target=path)
    print(json.dumps({"passed": not failures, "failures": failures}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
