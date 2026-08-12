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
import subprocess
import tempfile
from pathlib import Path

from lib.render.postprocess import format_number, postprocess

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
TEMPLATE_NAME = "final_report.md.j2"
CSS_NAME = "report.css"

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


# --- render chain (§15.3) -------------------------------------------------
#
# markdown -> pandoc -> HTML -> weasyprint -> PDF. Each step is a separate
# function returning an error string rather than raising: §22.3 makes
# `validate` the fatal gate, and a missing Pango on one machine should degrade
# the deliverable, not lose the assembled markdown that is already on disk.


def render_markdown(variables: dict, template_path: Path | None = None) -> str:
    """Render the report template, then apply the markdown fix-ups (§15.3).

    The fix-ups run here rather than in the caller because they are part of
    what "rendered markdown" means: pandoc reads alignment and alt text
    structurally, and a caller that forgot the pass would ship a report whose
    numeric columns rag left.
    """
    from jinja2 import Environment, FileSystemLoader

    path = template_path or TEMPLATES_DIR / TEMPLATE_NAME
    env = Environment(loader=FileSystemLoader(str(path.parent)),
                      keep_trailing_newline=True)
    env.filters["format_number"] = format_number
    return postprocess(env.get_template(path.name).render(**variables))


def to_html(md_path: Path, html_path: Path, *, pagetitle: str,
            css_path: Path | None = None) -> str | None:
    """Convert markdown to standalone HTML with pandoc. Returns an error
    string, or `None` on success.

    `pagetitle` sets `<title>` only. Pandoc's `title` metadata would
    additionally emit an `<h1 class="title">` block, which collides with the
    template's own masthead and gives every report two H1s.
    """
    css = css_path if css_path is not None else TEMPLATES_DIR / CSS_NAME
    cmd = ["pandoc", md_path.name, "-o", html_path.name, "--standalone",
           "--metadata", f"pagetitle={pagetitle}"]
    if css.exists():
        cmd += ["--include-in-header", str(css)]

    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       cwd=str(md_path.parent))
    except FileNotFoundError:
        return "pandoc not found on PATH (macOS: brew install pandoc)"
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()[:800]
        return f"pandoc HTML conversion failed: {detail}"
    return None


def to_pdf(html_path: Path, pdf_path: Path) -> str | None:
    """Convert the HTML to PDF with weasyprint. Returns an error string, or
    `None` on success.

    `base_url` is the HTML's own directory so relative chart paths resolve.
    """
    # weasyprint loads GLib/Pango through cffi; on macOS the Homebrew dylibs
    # are not on the default search path.
    brew_lib = "/opt/homebrew/lib"
    if Path(brew_lib).is_dir():
        existing = os.environ.get("DYLD_LIBRARY_PATH", "")
        if brew_lib not in existing.split(":"):
            os.environ["DYLD_LIBRARY_PATH"] = (
                f"{brew_lib}:{existing}" if existing else brew_lib)
    try:
        from weasyprint import HTML

        HTML(filename=str(html_path),
             base_url=str(html_path.parent)).write_pdf(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 — weasyprint raises OSError/ImportError/cffi errors
        detail = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:400]
        return f"weasyprint PDF conversion failed: {detail}"
    return None
