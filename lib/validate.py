"""The fatal validation gate (spec §8.4, §20).

Deterministic, model-free, fatal. Run at the bronze, silver and gold gates
(§22). There is no `--force`: a gate you can wave through is not a gate.

This module implements §8.4's checks. Task 4.1 covers:

1. producer contract — every JSON artifact conforms to its producer shape,
2. `fetch_cmd` — required on bronze, forbidden on `model`,
3. layer boundary — no model-written kinds in `sources/`, no model-shape JSON
   in `structured/`,
7. path containment — every artifact path resolves inside the ticker
   directory, and the ticker matches its pattern.

Checks 4 (citation resolution), 5 (derivation resolution) and 6 (secret
scanning) land in Task 4.2.

`wiki-lint` is deliberately NOT here: it is advisory (§22.1), and mixing
advisory findings into a fatal gate would either block builds on style or
train people to ignore the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from lib.manifest import MANIFEST_NAME
from lib.provenance import (
    BRONZE_KINDS,
    DERIVED_SUBDIR,
    MODEL_KINDS,
    StructuredMeta,
    check_compute_shape,
    check_fetch_shape,
    check_model_shape,
    read_structured,
)

# §8.4 check 7. Matched against the directory name as-is: a ticker directory is
# created upper-cased by `sra.py init`, so a lower-cased name on disk is itself
# a defect worth reporting.
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
MACRO_TICKER = "_MACRO"

_SHAPE_CHECKERS = {
    "fetch": check_fetch_shape,
    "compute": check_compute_shape,
    "model": check_model_shape,
}


@dataclass
class Finding:
    """One validation result. `error` fails the gate; `warning` is reported
    but does not (nothing emits warnings yet — the severity exists so a future
    check can be added without changing the exit-code contract)."""

    severity: str
    code: str
    path: str
    message: str


def _rel(path: Path, ticker_dir: Path) -> str:
    """Path for display, relative to the ticker dir when it is inside it."""
    try:
        return str(path.relative_to(ticker_dir))
    except ValueError:
        return str(path)


# --- check 7: path containment -------------------------------------------

def _check_containment(ticker_dir: Path, data_root: Path) -> list[Finding]:
    """§8.4 check 7: the ticker name matches its pattern, the ticker directory
    sits inside the data root, and every file inside RESOLVES inside it.

    Resolution, not spelling, is what matters: a symlink pointing outside would
    otherwise place evidence beyond the tree that `validate` inspects and
    `snapshot` captures, while still looking local in every listing.
    """
    findings: list[Finding] = []
    name = ticker_dir.name
    if name != MACRO_TICKER and not TICKER_RE.match(name):
        findings.append(Finding(
            "error", "path-containment", name,
            f"ticker directory name {name!r} does not match {TICKER_RE.pattern} "
            f"(only {MACRO_TICKER} is exempt)",
        ))

    root = data_root.resolve()
    resolved_dir = ticker_dir.resolve()
    if not resolved_dir.is_relative_to(root):
        findings.append(Finding(
            "error", "path-containment", str(ticker_dir),
            f"ticker directory resolves to {resolved_dir}, outside the data root {root}",
        ))
        return findings

    for path in ticker_dir.rglob("*"):
        if path.is_symlink() or path.is_file():
            if not path.resolve().is_relative_to(resolved_dir):
                findings.append(Finding(
                    "error", "path-containment", _rel(path, ticker_dir),
                    f"path resolves to {path.resolve()}, outside {resolved_dir}",
                ))
    return findings


# --- checks 1 and 2 over JSON artifacts ----------------------------------

def _iter_json(ticker_dir: Path) -> list[tuple[Path, bool]]:
    """Every durable JSON artifact, paired with whether it is bronze
    (`structured/`) or silver (`derived/`)."""
    out: list[tuple[Path, bool]] = [
        (p, True) for p in sorted((ticker_dir / "structured").rglob("*.json"))
    ]
    out += [(p, False) for p in sorted((ticker_dir / DERIVED_SUBDIR).rglob("*.json"))]
    return out


def _check_json_artifact(path: Path, is_bronze: bool, ticker_dir: Path) -> list[Finding]:
    rel = _rel(path, ticker_dir)
    try:
        meta, _ = read_structured(path)
    except ValueError as exc:
        # Covers both unparseable JSON and a missing `_meta`/`data` block:
        # `read_structured` is the one place that turns either into a message.
        return [Finding("error", "producer-shape", rel, str(exc))]

    findings: list[Finding] = []

    # Check 3, JSON half: model output is silver and must never sit in
    # structured/, which is where citations resolve.
    if is_bronze and meta.producer == "model":
        findings.append(Finding(
            "error", "layer-boundary", rel,
            "producer 'model' under structured/: model output is silver and "
            "must live under derived/ (§4.2, §6.3)",
        ))

    checker = _SHAPE_CHECKERS.get(meta.producer)
    if checker is None:
        findings.append(Finding(
            "error", "producer-shape", rel,
            f"unknown producer {meta.producer!r} (expected 'fetch', 'compute', or 'model')",
        ))
        return findings

    findings += [Finding("error", "producer-shape", rel, problem)
                 for problem in checker(meta)]
    findings += _check_fetch_cmd(meta, rel, is_bronze)
    return findings


def _check_fetch_cmd(meta: StructuredMeta, rel: str, is_bronze: bool) -> list[Finding]:
    """§8.4 check 2. The producer shapes already require `fetch_cmd` on
    `fetch`/`compute` and forbid it on `model`; this reports it under its own
    code so a missing reproduction command is legible as exactly that rather
    than as a generic shape problem."""
    if meta.producer == "model":
        if meta.fetch_cmd:
            return [Finding(
                "error", "fetch-cmd", rel,
                f"model artifact carries fetch_cmd {meta.fetch_cmd!r}: nothing "
                f"re-runs to reproduce model output (§6.2)",
            )]
        return []
    if not meta.fetch_cmd:
        layer = "bronze" if is_bronze else "silver"
        return [Finding(
            "error", "fetch-cmd", rel,
            f"{layer} {meta.producer} artifact has no fetch_cmd: it is what makes "
            f"the artifact reproducible rather than merely present (§6)",
        )]
    return []


# --- checks 2 and 3 over source documents --------------------------------

def _check_source_doc(path: Path, ticker_dir: Path) -> list[Finding]:
    rel = _rel(path, ticker_dir)
    try:
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [Finding("error", "producer-shape", rel, f"unreadable source: {exc}")]

    findings: list[Finding] = []
    kind = str(post.metadata.get("kind") or "")

    if kind in MODEL_KINDS:
        # §1.2's answer-chain defect: a research_answer under sources/ gets
        # catalogued and cited exactly like a filing, so a report citation can
        # terminate at model-generated text instead of evidence.
        findings.append(Finding(
            "error", "layer-boundary", rel,
            f"kind {kind!r} is model output and must not live under sources/ "
            f"(write it with write_answer, to derived/answers/ — §1.2, §5)",
        ))
    elif kind not in BRONZE_KINDS:
        findings.append(Finding(
            "error", "layer-boundary", rel,
            f"kind {kind!r} is not in BRONZE_KINDS (§5)",
        ))

    if not post.metadata.get("fetch_cmd"):
        findings.append(Finding(
            "error", "fetch-cmd", rel,
            "bronze source has no fetch_cmd: it is what makes the evidence "
            "reproducible rather than merely present (§6)",
        ))
    return findings


def _source_docs(ticker_dir: Path) -> list[Path]:
    """Current sources plus the archive — archived documents are still bronze
    and citations resolve into them (§5), so the same rules apply."""
    sources_dir = ticker_dir / "sources"
    if not sources_dir.is_dir():
        return []
    paths = [p for p in sorted(sources_dir.glob("*.md")) if p.name != MANIFEST_NAME]
    return paths + sorted((sources_dir / "archive").glob("*.md"))


# --- entry point ----------------------------------------------------------

def validate(ticker_dir: Path, data_root: Path) -> list[Finding]:
    """Run every §8.4 check over one ticker directory.

    Returns findings rather than raising, so the caller decides the exit code
    and every problem in the tree is reported in one pass — fixing violations
    one exception at a time would make a broken corpus take as many runs as it
    has defects.
    """
    findings = _check_containment(ticker_dir, data_root)
    for path, is_bronze in _iter_json(ticker_dir):
        findings += _check_json_artifact(path, is_bronze, ticker_dir)
    for path in _source_docs(ticker_dir):
        findings += _check_source_doc(path, ticker_dir)
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)
