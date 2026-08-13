"""Lint the rendered deliverables — `report.html` and `report.pdf` (§22.4).

Everything upstream of this point checks the report as *source*: hard checks run
over section drafts, `validate` resolves citations against bronze, the polish
chain reads markdown. Nothing looked at what a reader actually receives, and
that gap has a specific failure mode — the template machinery emitting itself
into the document.

The instance that motivated this module: `templates/report.css` is an HTML
fragment spliced into pandoc's `<head>`, and a block of rules was appended below
its closing `</style>`. Two things followed, both silent. The rules never
applied, so the citation-anchor styling shipped dead. And the CSS text — comments
and all — became character data in the head, which HTML5 error recovery moves
into the body, so it rendered as prose in the HTML and printed in the PDF.
Neither pandoc nor weasyprint has any reason to complain: both were handed
well-formed input and did exactly what it said.

So the check is not "is the HTML valid". It is narrower and answers the question
no upstream gate asks: **does the reader's document contain the machinery that
produced it?** That is mechanical to detect and belongs here rather than in a
model's judgment (§22.1) — a regex knows a CSS declaration block when it sees
one, and the visual pass in `/sra-assemble` is for what a regex cannot see.

Deliberately NOT a general HTML validator. Pandoc's output is well-formed by
construction and a validator would drown a real finding in advisory noise.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

# Elements whose character data is *supposed* to be CSS or JavaScript. Text
# inside these is the only place machinery may legitimately live.
RAW_TEXT_ELEMENTS = frozenset({"style", "script"})

# CSS signatures, hunted in text that reached the reader. Each is something no
# equity research prose contains, which is what keeps the false-positive rate at
# zero on real reports:
#
#   comment      /* ... */ — the giveaway in the motivating bug
#   at-rule      @media / @page / @font-face / @import
#   custom prop  --navy: #0f2942  (a `:root` block's contents)
#   declaration  { font-size: .72em; }  — a brace holding `prop: value`
#
# The declaration pattern is the load-bearing one: it fires on a rule block even
# when the selector above it looks like ordinary words.
CSS_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("css comment", r"/\*.*?\*/"),
    ("css at-rule", r"@(?:media|page|font-face|import|supports|keyframes)\b"),
    ("css custom property", r"--[a-z][a-z0-9-]*\s*:\s*[^;\n]{1,60};"),
    ("css declaration block", r"\{[^{}]*?[a-z-]{3,}\s*:\s*[^{};]{1,60}[;}]"),
)

# Template markers that survived rendering. A `{{ ... }}` in the deliverable
# means Jinja was handed a variable it did not have, and the reader gets the
# variable name where the number belonged.
TEMPLATE_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("unrendered jinja expression", r"\{\{.{0,80}?\}\}"),
    ("unrendered jinja statement", r"\{%.{0,80}?%\}"),
    ("literal html comment", r"<!--.{0,80}?-->"),
)

MAX_EXCERPT = 90
MAX_FINDINGS_PER_RULE = 3


class _VisibleTextParser(HTMLParser):
    """Collect character data that is NOT inside a `<style>` or `<script>`.

    `HTMLParser` switches to CDATA mode on those elements by itself, but it does
    not tell the caller which mode produced a given `handle_data` call — so the
    depth counter here is what separates "CSS in a stylesheet" from "CSS printed
    at the reader". A stray `</style>` drops the counter back to zero and every
    subsequent rule lands in `self.text`, which is exactly the bug this catches.

    `convert_charrefs` is left on so `&amp;` does not read as a finding.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self._raw_depth = 0
        self.style_opens = 0
        self.style_closes = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        del attrs  # only the tag name matters; the name is fixed by the base class
        if tag in RAW_TEXT_ELEMENTS:
            self._raw_depth += 1
        if tag == "style":
            self.style_opens += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in RAW_TEXT_ELEMENTS:
            # Never below zero: a stray `</style>` with no opener is itself a
            # defect, and clamping keeps the counter honest for what follows.
            self._raw_depth = max(0, self._raw_depth - 1)
        if tag == "style":
            self.style_closes += 1

    def handle_data(self, data: str) -> None:
        if self._raw_depth == 0:
            self.text.append(data)

    def visible_text(self) -> str:
        return "".join(self.text)


def _excerpt(match: str) -> str:
    """One-line, length-capped quotation of what was found."""
    flat = " ".join(match.split())
    if len(flat) > MAX_EXCERPT:
        flat = flat[:MAX_EXCERPT - 1] + "…"
    return flat


def scan_text(text: str, *, where: str,
              signatures: tuple[tuple[str, str], ...]) -> list[str]:
    """Findings for one body of reader-visible text.

    Reports the excerpt rather than just the rule name: the fix is always in a
    template, and the quoted text is what makes it findable.
    """
    problems: list[str] = []
    for label, pattern in signatures:
        seen: list[str] = []
        for match in re.finditer(pattern, text, re.DOTALL | re.IGNORECASE):
            excerpt = _excerpt(match.group(0))
            if excerpt not in seen:
                seen.append(excerpt)
            if len(seen) >= MAX_FINDINGS_PER_RULE:
                break
        for excerpt in seen:
            problems.append(f"{where}: {label} visible to the reader — {excerpt!r}")
    return problems


def lint_html(html: str, *, where: str = "report.html") -> list[str]:
    """Problems in the rendered HTML. Empty list means clean."""
    parser = _VisibleTextParser()
    parser.feed(html)
    parser.close()

    problems: list[str] = []
    # Checked before the text scan because it names the CAUSE, and a reader of
    # the build log should meet the cause before the symptoms.
    if parser.style_opens != parser.style_closes:
        problems.append(
            f"{where}: {parser.style_opens} <style> tag(s) but "
            f"{parser.style_closes} </style> — the stylesheet fragment "
            "(templates/report.css) is unbalanced, so its rules leak into the "
            "document as text")

    problems += scan_text(parser.visible_text(), where=where,
                          signatures=CSS_SIGNATURES + TEMPLATE_SIGNATURES)
    return problems


def pdf_text(pdf_path: Path) -> tuple[str | None, str | None]:
    """`(text, skip_reason)` for a rendered PDF.

    Uses poppler's `pdftotext`, which is treated exactly like pandoc and
    weasyprint (§22.3): a machine without it degrades the check into a reported
    skip rather than failing a build over a missing system tool. The PDF is
    rendered *from* the linted HTML, so a skip here loses very little — what it
    costs is the print-only surface, where `@page` rules and running headers
    apply and the HTML view cannot see them.
    """
    if not pdf_path.exists():
        return None, f"{pdf_path.name} was not rendered"
    if shutil.which("pdftotext") is None:
        return None, ("pdftotext not on PATH (macOS: brew install poppler) — "
                      "PDF text not checked")
    try:
        done = subprocess.run(["pdftotext", str(pdf_path), "-"],
                              check=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, "pdftotext timed out"
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()[:200]
        return None, f"pdftotext failed: {detail}"
    return done.stdout.decode("utf-8", errors="replace"), None


def lint_pdf(pdf_path: Path) -> tuple[list[str], str | None]:
    """`(problems, skip_reason)` for the rendered PDF."""
    text, skip = pdf_text(pdf_path)
    if text is None:
        return [], skip
    return scan_text(text, where=pdf_path.name,
                     signatures=CSS_SIGNATURES + TEMPLATE_SIGNATURES), None


def lint_rendered(run_dir: Path, *, stem: str = "report") -> dict:
    """Lint both deliverables in a run directory.

    Returns `{"problems": [...], "skipped": [...], "checked": [...]}`. A
    deliverable that was never rendered is a `skipped` entry, not a problem:
    §22.3 already degrades a missing pandoc into a reported error, and this
    module must not convert that degradation into a second failure.
    """
    html_path = run_dir / f"{stem}.html"
    pdf_path = run_dir / f"{stem}.pdf"

    problems: list[str] = []
    skipped: list[str] = []
    checked: list[str] = []

    if html_path.exists():
        checked.append(html_path.name)
        problems += lint_html(html_path.read_text(encoding="utf-8",
                                                 errors="replace"),
                              where=html_path.name)
    else:
        skipped.append(f"{html_path.name} was not rendered")

    pdf_problems, pdf_skip = lint_pdf(pdf_path)
    problems += pdf_problems
    if pdf_skip:
        skipped.append(pdf_skip)
    else:
        checked.append(pdf_path.name)

    return {"problems": problems, "skipped": skipped, "checked": checked}
