"""Rendered-deliverable lint (spec §22.4).

The defect this module exists for shipped once: `templates/report.css` is an
HTML fragment, a block of rules was appended below its closing style tag, and
the result was a report whose stylesheet — comments included — printed as body
copy in both the HTML and the PDF. Nothing upstream noticed, because pandoc and
weasyprint were each handed well-formed input.

Two kinds of test here. Most exercise the detectors against synthetic HTML. The
last group is the one that actually prevents a recurrence: it asserts the real
`templates/report.css` is a balanced fragment, so appending below the close tag
fails offline in milliseconds rather than at the end of a 60-minute build.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.render.assemble import CSS_NAME, TEMPLATES_DIR
from lib.render.lint_render import lint_html, lint_rendered, scan_text
from lib.render.lint_render import CSS_SIGNATURES, TEMPLATE_SIGNATURES

ALL_SIGNATURES = CSS_SIGNATURES + TEMPLATE_SIGNATURES


def _page(head: str = "", body: str = "") -> str:
    return f"<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>"


# --- the motivating defect -------------------------------------------------

def test_rules_below_the_close_tag_are_reported():
    """The exact shape of the shipped bug: a fragment that closes early."""
    head = "<style>p { color: red; }</style>\n.cite a { font-size: .72em; }"
    problems = lint_html(_page(head=head))
    assert problems, "CSS below the close tag must be reported"
    assert any("declaration block" in p for p in problems)


def test_unbalanced_style_tags_name_the_cause():
    """The count mismatch is reported before the symptoms, and names the file."""
    problems = lint_html(_page(head="<style>p { color: red; }</style></style>"))
    assert any("templates/report.css" in p and "unbalanced" in p
               for p in problems)


def test_stylesheet_comment_reaching_the_reader_is_reported():
    body = "<p>/* Half a line between reference entries. */</p>"
    assert any("css comment" in p for p in lint_html(_page(body=body)))


def test_close_tag_inside_a_css_comment_still_ends_the_element():
    """HTML ends a style element at the first close sequence; CSS comment
    syntax does not protect it. This is the same defect wearing a different
    hat, and it was introduced *while* fixing the original one."""
    head = "<style>/* never write the close tag here: </style> */ p { color: red; }</style>"
    assert lint_html(_page(head=head)), (
        "a close tag inside a CSS comment terminates the element and spills "
        "the rest into the document")


# --- no false positives ----------------------------------------------------

def test_css_inside_a_style_element_is_not_a_finding():
    head = ("<style>/* palette */ :root { --navy: #0f2942; } "
            "@media print { p { color: #000; } }</style>")
    assert lint_html(_page(head=head, body="<p>Revenue grew 12%.</p>")) == []


def test_ordinary_research_prose_is_not_a_finding():
    body = ("<p>Revenue grew 12% to $2.1bn; the gross margin held at 74.5% "
            "[REPORTED] as of 2026-06-30. Management guided to 10–12% "
            "growth {see Section 6}.</p>")
    assert lint_html(_page(body=body)) == []


def test_script_contents_are_not_a_finding():
    head = '<script>const s = {a: 1}; /* not prose */</script>'
    assert lint_html(_page(head=head)) == []


# --- the other leak classes ------------------------------------------------

@pytest.mark.parametrize("leak,expected", [
    ("{{ company_name }}", "unrendered jinja expression"),
    ("{% if verdict %}", "unrendered jinja statement"),
    ("<!-- TODO: fix the masthead -->", "literal html comment"),
    ("@page { size: A4; }", "css at-rule"),
    ("--navy: #0f2942;", "css custom property"),
])
def test_template_machinery_is_reported(leak, expected):
    problems = scan_text(leak, where="report.pdf", signatures=ALL_SIGNATURES)
    assert any(expected in p for p in problems), problems


def test_findings_quote_what_was_found():
    """The fix is always in a template; the excerpt is what makes it findable."""
    problems = scan_text("/* citation anchors */", where="report.pdf",
                         signatures=ALL_SIGNATURES)
    assert "citation anchors" in problems[0]


def test_repeated_findings_are_capped_per_rule():
    text = " ".join(f"/* comment {n} */" for n in range(20))
    problems = scan_text(text, where="report.pdf", signatures=ALL_SIGNATURES)
    assert 0 < len(problems) <= 3


# --- lint_rendered over a run directory ------------------------------------

def test_absent_deliverables_are_skipped_not_failed(tmp_path: Path):
    """§22.3 already degrades a missing renderer into a reported error; this
    module must not convert that degradation into a second failure."""
    result = lint_rendered(tmp_path)
    assert result["problems"] == []
    assert result["checked"] == []
    assert any("report.html" in s for s in result["skipped"])


def test_clean_html_run_reports_checked(tmp_path: Path):
    (tmp_path / "report.html").write_text(_page(body="<p>Revenue grew 12%.</p>"),
                                          encoding="utf-8")
    result = lint_rendered(tmp_path)
    assert result["problems"] == []
    assert "report.html" in result["checked"]


def test_dirty_html_run_reports_problems(tmp_path: Path):
    (tmp_path / "report.html").write_text(
        _page(head="<style>p{color:red}</style>.cite a { font-size: .72em; }"),
        encoding="utf-8")
    assert lint_rendered(tmp_path)["problems"]


# --- the guard that prevents a recurrence ----------------------------------

def _css_fragment() -> str:
    return (TEMPLATES_DIR / CSS_NAME).read_text(encoding="utf-8")


def test_css_template_is_a_balanced_html_fragment():
    """`report.css` is spliced into pandoc's head verbatim, so it must open and
    close exactly one style element. Appending rules below the close tag is the
    bug this whole module exists for — it fails here, offline, in milliseconds.
    """
    css = _css_fragment()
    assert css.count("<style>") == 1, "the fragment must open exactly one style element"
    assert css.count("</style>") == 1, (
        "the fragment must contain exactly one close tag — and never inside a "
        "comment, where HTML still honors it")


def test_css_template_closes_on_its_last_line():
    css = _css_fragment().rstrip()
    assert css.endswith("</style>"), (
        "rules appended below the close tag never apply and render as body "
        "copy; append them above it")


def test_css_template_produces_no_findings_when_wrapped_in_a_page():
    """The fragment as head content, which is exactly how pandoc uses it."""
    assert lint_html(_page(head=_css_fragment(),
                           body="<p>Revenue grew 12%.</p>")) == []
