# Report Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six defect classes found in the SPCX 2026-08-12 report — tripled exhibits, dead `[^N]` markers, empty peer tables, prose compressed into gibberish, an unreconciled verdict, and self-reference as "the analyst".

**Architecture:** Five of the six fixes are deterministic and land in the render/validate layer, each paired with a fatal gate so the defect cannot recur. The sixth (prose clarity) loosens the polish word gate from per-section to report-level and adds the first pipeline stage that reads the *assembled* report. Every new gate is model-free and lives in `lib/`, never in a skill.

**Tech Stack:** Python 3.12, `uv`, pytest, jinja2 templates, pandoc → weasyprint, argparse CLI in `sra.py`.

**Spec:** `docs/superpowers/specs/2026-08-12-report-quality-design.md`

## Global Constraints

- `uv run pytest -q -m "not integration"` must stay green after every task.
- `pathlib.Path` and type hints everywhere; no bare `except:`.
- Data functions return `(success, data, error_msg)`; `main()` returns an exit code.
- Sources are immutable — never overwrite a fetched artifact.
- Deterministic work goes in `sra.py` / `lib/`, never into a skill or a prompt.
- Advisory findings never fail a gate; `lib/validate.py` is fatal-only by design.
- Run tests with `uv run pytest`, never bare `pytest`.
- Commit after each task with a `feat:`, `fix:` or `docs:` prefix.

---

## File Structure

**Created:**
- `lib/verdict_checks.py` — verdict/valuation reconciliation gate (Task 7)
- `prompts/polish/clarity.md` — assembled-report clarity critique (Task 10)
- `tests/test_assemble_charts.py` (Task 1)
- `tests/test_postprocess_citations.py` (Task 3)
- `tests/test_prefetch_peers.py` (Task 5)
- `tests/test_verdict_checks.py` (Task 7)
- `tests/test_hard_checks_report_length.py` (Task 9)

**Modified:**
- `lib/render/assemble.py` — dashboard-chart exclusion, `_body` dedupe, peer warning
- `lib/render/postprocess.py` — `link_citations` in the `postprocess` chain
- `lib/validate.py` — duplicate-image, dangling-citation and verdict gates
- `lib/hard_checks.py` — `report_not_longer_than`, `not_longer_than_pct`
- `templates/final_report.md.j2` — appendix gallery removal, voice strings
- `templates/report.css` — citation and reference styling
- `sra.py` — `prefetch-peers` command
- `sections.yaml` — voice rule on all seven sections
- `prompts/polish/polish.md`, `prompts/write/valuation.md`, `prompts/polish/conclusion.md`, `prompts/polish/evaluate.md`
- `STYLE.md`, `CLAUDE.md`
- `.claude/skills/sra-build/SKILL.md`, `sra-update`, `sra-assemble`
- `workflows/polish_chain.js`

---

### Task 1: Stop the chartbook re-placing dashboard charts

`price_weekly` and `income_sankey` are hardcoded in the dashboard template. When the chartbook selector also picks them, `_body()` embeds them a second time. This task makes the selector's choice a no-op and makes `_body()` refuse any repeat.

**Files:**
- Modify: `lib/render/assemble.py` (`load_chartbook` ~line 258, `_figure`/`_body` ~line 346, caller ~line 535)
- Test: `tests/test_assemble_charts.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `DASHBOARD_CHARTS: frozenset[str]` — module constant in `lib.render.assemble`
  - `load_chartbook(ticker_dir: Path) -> tuple[list[dict], list[str], list[str]]` — `(exhibits, problems, warnings)`; was a 2-tuple
  - `_body(drafts: dict[str, str], exhibits: list[dict], run_dir: Path) -> tuple[str, list[str]]` — `(markdown, warnings)`; was `-> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assemble_charts.py`:

```python
"""Exhibit placement: each chart lands in the report exactly once."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.render.assemble import DASHBOARD_CHARTS, _body, load_chartbook


def _ticker_dir(tmp_path: Path, selections: list[dict]) -> Path:
    d = tmp_path / "TEST"
    candidates = d / "charts" / "candidates"
    candidates.mkdir(parents=True)
    for name in {"price_weekly", "income_sankey", "revenue_growth"}:
        (candidates / f"{name}.png").write_bytes(b"\x89PNG")
    (d / "charts" / "chartbook.json").write_text(
        json.dumps({"selected": selections}), encoding="utf-8")
    return d


def test_dashboard_chart_is_dropped_with_a_warning(tmp_path: Path) -> None:
    d = _ticker_dir(tmp_path, [
        {"name": "price_weekly", "section": "profile", "order": 1, "caption": "c"},
        {"name": "revenue_growth", "section": "financial", "order": 2, "caption": "c"},
    ])
    exhibits, problems, warnings = load_chartbook(d)

    assert problems == []
    assert [e["name"] for e in exhibits] == ["revenue_growth"]
    assert any("price_weekly" in w for w in warnings)


def test_dropping_a_dashboard_chart_does_not_leave_a_numbering_gap(tmp_path: Path) -> None:
    d = _ticker_dir(tmp_path, [
        {"name": "income_sankey", "section": "business_model", "order": 1, "caption": "c"},
        {"name": "revenue_growth", "section": "financial", "order": 2, "caption": "c"},
    ])
    exhibits, _, _ = load_chartbook(d)

    assert [e["number"] for e in exhibits] == [1]


def test_body_embeds_each_image_once(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    png = tmp_path / "TEST" / "charts" / "candidates" / "revenue_growth.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG")

    exhibit = {"name": "revenue_growth", "section": "financial", "order": 1.0,
               "caption": "c", "png": png, "number": 1}
    drafts = {sid: f"## {sid}\n" for sid in
              ("profile", "business_model", "competitive", "financial",
               "supply_chain", "valuation", "risk_news")}

    markdown, warnings = _body(drafts, [exhibit, dict(exhibit, number=2)], run_dir)

    assert markdown.count("revenue_growth.png") == 1
    assert len(warnings) == 1


def test_dashboard_charts_names_the_two_template_placed_charts() -> None:
    assert DASHBOARD_CHARTS == frozenset({"price_weekly", "income_sankey"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_assemble_charts.py -v`
Expected: FAIL — `ImportError: cannot import name 'DASHBOARD_CHARTS'`

- [ ] **Step 3: Add the constant and the exclusion**

In `lib/render/assemble.py`, beside `CHARTBOOK_NAME` (~line 49):

```python
# §16.2: the dashboard template places these two itself, so a chartbook that
# also selects one produces the same image twice in one report.
DASHBOARD_CHARTS: frozenset[str] = frozenset({"price_weekly", "income_sankey"})
```

Change `load_chartbook`'s docstring return line and signature to
`-> tuple[list[dict], list[str], list[str]]`, add `warnings: list[str] = []`
beside `problems`, and insert this branch in the selection loop immediately
after the `section not in SECTION_IDS` check:

```python
        if name in DASHBOARD_CHARTS:
            warnings.append(
                f"{CHARTBOOK_NAME}: selection {name!r} is already placed by the "
                f"dashboard; dropping it from the inline exhibits")
            continue
```

Change the two early `return` statements in `load_chartbook` (the absent-file
and malformed-payload paths) to return three values, and the final one to
`return exhibits, problems, warnings`.

- [ ] **Step 4: Make `_body` refuse repeats**

Replace `_body` in `lib/render/assemble.py`:

```python
def _body(drafts: dict[str, str], exhibits: list[dict],
          run_dir: Path) -> tuple[str, list[str]]:
    """Sections in `SECTION_IDS` order, each followed by its exhibits (§16.2).

    An image already embedded is skipped rather than repeated: the selector is
    a model and the template places two charts of its own, so this is the last
    place that can guarantee one placement per chart.
    """
    parts: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for section_id in SECTION_IDS:
        parts.append(drafts[section_id].rstrip() + "\n")
        for exhibit in exhibits:
            if exhibit["section"] != section_id:
                continue
            path = _relative(exhibit["png"], run_dir)
            if path in seen:
                warnings.append(
                    f"exhibit {exhibit['name']!r} is already embedded earlier "
                    f"in the report; skipping the repeat")
                continue
            seen.add(path)
            parts.append("\n" + _figure(
                path, f"Exhibit {exhibit['number']} — {exhibit['caption']}"))
    return "\n".join(parts), warnings
```

- [ ] **Step 5: Update the caller**

In `lib/render/assemble.py`, change line ~535:

```python
    exhibits, problems, chart_warnings = load_chartbook(ticker_dir)
```

and line ~560:

```python
    body, body_warnings = _body(drafts, exhibits, run_dir)
```

Add to the `data` dict built near line ~592, after `"exhibits": len(exhibits),`:

```python
        "warnings": chart_warnings + body_warnings,
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_assemble_charts.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full suite for regressions**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS. `tests/test_assemble_skill_contract.py` may call `load_chartbook`; if it unpacks two values, update it to three.

- [ ] **Step 8: Commit**

```bash
git add lib/render/assemble.py tests/test_assemble_charts.py
git commit -m "fix: place each chart exactly once in the assembled report"
```

---

### Task 2: Delete the appendix gallery and gate duplicate images

The appendix "Chartbook" section re-rendered every exhibit at full size — a verbatim third copy. Removing it is the fix; the validate gate is what keeps it fixed.

**Files:**
- Modify: `templates/final_report.md.j2:73-75` (TOC entry), `:229-245` (gallery block)
- Modify: `lib/validate.py` (new check + registration in `validate()` ~line 613)
- Modify: `prompts/chartbook.md`
- Test: `tests/test_assemble_charts.py` (extend)

**Interfaces:**
- Consumes: `DASHBOARD_CHARTS` from Task 1.
- Produces: `_check_report_exhibits(ticker_dir: Path) -> list[Finding]` in `lib.validate`, emitting code `exhibit-duplicated` at severity `error`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assemble_charts.py`:

```python
from lib.validate import validate


def _report_tree(tmp_path: Path, body: str) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    d = data_root / "TEST"
    run = d / "reports" / "2026-08-12"
    run.mkdir(parents=True)
    (run / "report.md").write_text(body, encoding="utf-8")
    (run / "citation_map.json").write_text("{}", encoding="utf-8")
    return d, data_root


def test_repeated_image_is_a_fatal_finding(tmp_path: Path) -> None:
    d, data_root = _report_tree(tmp_path, (
        "![](../../charts/candidates/income_sankey.png)\n\n"
        "text\n\n"
        "![](../../charts/candidates/income_sankey.png)\n"))

    findings = [f for f in validate(d, data_root) if f.code == "exhibit-duplicated"]

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "income_sankey.png" in findings[0].message


def test_distinct_images_produce_no_finding(tmp_path: Path) -> None:
    d, data_root = _report_tree(tmp_path, (
        "![](../../charts/candidates/income_sankey.png)\n\n"
        "![](../../charts/candidates/price_weekly.png)\n"))

    assert [f for f in validate(d, data_root) if f.code == "exhibit-duplicated"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_assemble_charts.py -k exhibit -v`
Expected: FAIL — no finding with code `exhibit-duplicated`

- [ ] **Step 3: Add the validate check**

In `lib/validate.py`, add `from collections import Counter` to the imports and
this check after `_check_assembled_reports`:

```python
_IMAGE_TARGET_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")


def _check_report_exhibits(ticker_dir: Path) -> list[Finding]:
    """Every image in an assembled report appears exactly once.

    Three independent emitters place charts — the dashboard template, the
    per-section exhibit embeds, and (historically) an appendix gallery. Any of
    them can regress; this is the one place that sees the finished document.
    """
    findings: list[Finding] = []
    for report in sorted((ticker_dir / "reports").glob("*/report.md")):
        rel = _rel(report, ticker_dir)
        counts = Counter(_IMAGE_TARGET_RE.findall(
            report.read_text(encoding="utf-8")))
        for target, seen in sorted(counts.items()):
            if seen > 1:
                findings.append(Finding(
                    "error", "exhibit-duplicated", rel,
                    f"image {target} appears {seen} times; each exhibit must "
                    f"be placed exactly once (§16.2)",
                ))
    return findings
```

Register it in `validate()` after the `_check_assembled_reports` line:

```python
    findings += _check_report_exhibits(ticker_dir)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_assemble_charts.py -k exhibit -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Delete the appendix gallery from the template**

In `templates/final_report.md.j2`, delete these three lines from the TOC block
(around line 73):

```jinja
{%- if chartbook %}
    <li><a href="#chartbook">Chartbook</a></li>
{%- endif %}
```

and delete this entire block (around line 229):

```jinja
{% if chartbook %}

---

<div style="page-break-after: always;"></div>

## Chartbook

{% for exhibit in chartbook %}
::: {.figure}
![]({{ exhibit.path }})

<span class="caption">Exhibit {{ loop.index }} — {{ exhibit.caption }}</span>
:::

{% endfor %}
{% endif %}
```

Leave the `chartbook` variable in `build_variables` — `run_stats` and the
skills read the exhibit count from it.

- [ ] **Step 6: Tell the selector those slots are taken**

In `prompts/chartbook.md`, add under the selection rules:

```markdown
Two slots are already filled. The report's dashboard places the weekly price
chart (`price_weekly`) and the income-statement Sankey (`income_sankey`) itself,
before Section 1. Selecting either wastes an exhibit slot — the assembler drops
it, and the report shows the chart once, where the dashboard put it.
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add templates/final_report.md.j2 lib/validate.py prompts/chartbook.md tests/test_assemble_charts.py
git commit -m "fix: drop the appendix chart gallery and gate duplicate exhibits"
```

---

### Task 3: Turn `[^N]` into clickable anchors

Sections carry `[^N]` markers but `references.md` is a plain `[N] …` list, so pandoc sees no footnote definitions and the markers survive as literal text. Native pandoc footnotes are unusable — `[^2]` has eleven call sites in the SPCX report and a pandoc footnote may be referenced once.

**Files:**
- Modify: `lib/render/postprocess.py` (new function + `postprocess` chain ~line 255)
- Test: `tests/test_postprocess_citations.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `link_citations(markdown: str) -> str` in `lib.render.postprocess`, exported through the existing `postprocess(markdown: str) -> str` chain (signature unchanged).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_postprocess_citations.py`:

```python
"""Citation markers become bidirectional anchor links."""

from __future__ import annotations

from lib.render.postprocess import link_citations

REFS = "## References\n\n[1] First source — example.com\n[2] Second source — example.org\n"


def test_single_citation_gets_one_backlink() -> None:
    out = link_citations(f"Claim.[^1]\n\n{REFS}")

    assert '<sup class="cite"><a id="cite-1-1" href="#ref-1">1</a></sup>' in out
    assert '<span class="ref-n" id="ref-1">[1]</span>' in out
    assert out.count('href="#cite-1-1"') == 1
    assert "↩¹" not in out


def test_repeated_citation_gets_numbered_backlinks() -> None:
    out = link_citations(f"A.[^2] B.[^2] C.[^2]\n\n{REFS}")

    for k in (1, 2, 3):
        assert f'id="cite-2-{k}"' in out
        assert f'href="#cite-2-{k}"' in out
    assert "↩¹" in out and "↩²" in out and "↩³" in out


def test_no_marker_survives_for_a_resolvable_citation() -> None:
    out = link_citations(f"A.[^1] B.[^2]\n\n{REFS}")

    body = out.split("## References")[0]
    assert "[^" not in body


def test_dangling_marker_is_left_literal() -> None:
    out = link_citations(f"A.[^99]\n\n{REFS}")

    assert "[^99]" in out


def test_reference_text_is_preserved() -> None:
    out = link_citations(f"A.[^1]\n\n{REFS}")

    assert "First source — example.com" in out


def test_document_without_references_is_unchanged() -> None:
    text = "A claim.[^1]\n"
    assert link_citations(text) == text


def test_postprocess_chain_applies_it() -> None:
    from lib.render.postprocess import postprocess

    assert 'href="#ref-1"' in postprocess(f"Claim.[^1]\n\n{REFS}")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_postprocess_citations.py -v`
Expected: FAIL — `ImportError: cannot import name 'link_citations'`

- [ ] **Step 3: Implement `link_citations`**

Add to `lib/render/postprocess.py`, after `blank_image_alts` and before
`postprocess`:

```python
# --- citation anchors -----------------------------------------------------

_CITE_RE = re.compile(r"\[\^(\d+)\]")
_REF_ENTRY_RE = re.compile(r"^\[(\d+)\]\s")
_REFERENCES_HEADING = "## References"
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


def _superscript(n: int) -> str:
    return "".join(_SUPERSCRIPT_DIGITS[int(digit)] for digit in str(n))


def _backlinks(number: str, total: int) -> str:
    """Return arrows from a reference entry to each of its call sites.

    One citation gets a bare arrow; several get numbered arrows, because
    "which of these eleven mentions did I come from" is otherwise unanswerable.
    """
    if total == 0:
        return ""
    if total == 1:
        return f' <a class="backref" href="#cite-{number}-1">↩</a>'
    return " " + " ".join(
        f'<a class="backref" href="#cite-{number}-{k}">↩{_superscript(k)}</a>'
        for k in range(1, total + 1))


def link_citations(markdown: str) -> str:
    """Make `[^N]` markers clickable, with return links from the references.

    Pandoc's own footnotes are not usable here: a pandoc footnote may be
    referenced once, and a heavily-cited source carries a dozen call sites. So
    the markers become explicit anchors into the single References list, which
    also keeps one canonical entry per source.

    A marker with no matching entry is left exactly as it was — `validate`
    fails the build on a surviving `[^`, which is a stronger signal than a
    silently swallowed citation.
    """
    head, sep, tail = markdown.partition(_REFERENCES_HEADING)
    if not sep:
        return markdown

    entries = {m.group(1) for m in
               (_REF_ENTRY_RE.match(line) for line in tail.splitlines()) if m}
    counts: dict[str, int] = {}

    def _mark(match: re.Match) -> str:
        number = match.group(1)
        if number not in entries:
            return match.group(0)
        counts[number] = counts.get(number, 0) + 1
        return (f'<sup class="cite"><a id="cite-{number}-{counts[number]}" '
                f'href="#ref-{number}">{number}</a></sup>')

    head = _CITE_RE.sub(_mark, head)

    lines: list[str] = []
    for line in tail.splitlines():
        match = _REF_ENTRY_RE.match(line)
        if match is None:
            lines.append(line)
            continue
        number = match.group(1)
        lines.append(f'<span class="ref-n" id="ref-{number}">[{number}]</span> '
                     f'{line[match.end():]}{_backlinks(number, counts.get(number, 0))}')

    rebuilt = "\n".join(lines) + ("\n" if tail.endswith("\n") else "")
    return head + sep + rebuilt
```

- [ ] **Step 4: Add it to the chain**

In `postprocess()`, insert the call before `blank_image_alts` — it must run
after the table passes so it never rewrites a cell mid-alignment:

```python
    markdown = colour_signed_cells(markdown)
    markdown = link_citations(markdown)
    return blank_image_alts(markdown)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_postprocess_citations.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lib/render/postprocess.py tests/test_postprocess_citations.py
git commit -m "feat: link citation markers to references with return anchors"
```

---

### Task 4: Style the citations and gate the dangling ones

Anchors that land under a header, or references packed with no vertical space, are still unreadable. And a `[^N]` with no entry must fail the build rather than ship as visible garbage.

**Files:**
- Modify: `templates/report.css`
- Modify: `lib/validate.py` (extend `_check_report_exhibits`'s neighbourhood)
- Test: `tests/test_assemble_charts.py` (extend)

**Interfaces:**
- Consumes: `link_citations` from Task 3, `_rel` and `Finding` from `lib.validate`.
- Produces: `_check_dangling_citations(ticker_dir: Path) -> list[Finding]` emitting code `citation-unlinked`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assemble_charts.py` (it already has `_report_tree`):

```python
def test_dangling_citation_marker_is_fatal(tmp_path: Path) -> None:
    d, data_root = _report_tree(
        tmp_path, "A claim.[^7]\n\n## References\n\n[1] Only source\n")

    findings = [f for f in validate(d, data_root) if f.code == "citation-unlinked"]

    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_linked_citations_produce_no_finding(tmp_path: Path) -> None:
    d, data_root = _report_tree(tmp_path, (
        'A claim.<sup class="cite"><a id="cite-1-1" href="#ref-1">1</a></sup>\n\n'
        "## References\n\n"
        '<span class="ref-n" id="ref-1">[1]</span> Only source\n'))

    assert [f for f in validate(d, data_root) if f.code == "citation-unlinked"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_assemble_charts.py -k dangling -v`
Expected: FAIL — no finding with code `citation-unlinked`

- [ ] **Step 3: Add the check**

In `lib/validate.py`, after `_check_report_exhibits`:

```python
_LITERAL_MARKER_RE = re.compile(r"\[\^[^\]\s]+\]")


def _check_dangling_citations(ticker_dir: Path) -> list[Finding]:
    """No raw `[^…]` marker survives into an assembled report.

    `link_citations` rewrites every marker whose number has a References entry.
    Anything still literal at this point resolves to nothing and would print in
    the PDF as visible punctuation the reader cannot act on.
    """
    findings: list[Finding] = []
    for report in sorted((ticker_dir / "reports").glob("*/report.md")):
        markers = list(dict.fromkeys(_LITERAL_MARKER_RE.findall(
            report.read_text(encoding="utf-8"))))
        if markers:
            findings.append(Finding(
                "error", "citation-unlinked", _rel(report, ticker_dir),
                f"{len(markers)} citation marker(s) resolve to no reference "
                f"entry and would print literally: {', '.join(markers[:5])}",
            ))
    return findings
```

Register it in `validate()` beside the exhibit check:

```python
    findings += _check_report_exhibits(ticker_dir)
    findings += _check_dangling_citations(ticker_dir)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_assemble_charts.py -k "dangling or linked" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add the CSS**

Append to `templates/report.css`:

```css
/* --- citation anchors ---------------------------------------------------
   Markers are superscript links into the References list; each entry links
   back to every place it was cited. `scroll-margin-top` keeps a jump target
   clear of the sticky header instead of landing underneath it. */

.cite a {
  font-size: .72em;
  vertical-align: super;
  line-height: 0;
  text-decoration: none;
  color: var(--info);
  padding: 0 .08em;
}

.cite a:hover { text-decoration: underline; }

.ref-n { font-weight: 600; }

.backref {
  text-decoration: none;
  color: var(--info);
  font-size: .85em;
  margin-left: .2em;
}

[id^="cite-"], [id^="ref-"] { scroll-margin-top: 1.5rem; }

[id^="cite-"]:target, [id^="ref-"]:target {
  background: #fff3bf;
  border-radius: 2px;
}

/* Half a line between reference entries — the dense list is unreadable. */
#references p, #references li { margin-bottom: .6em; }
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add templates/report.css lib/validate.py tests/test_assemble_charts.py
git commit -m "feat: style citation anchors and fail on unresolved markers"
```

---

### Task 5: Fetch fundamentals for the selected peers

`_peer_row()` reads `data/<PEER>/structured/`. Peer selection runs *after* prefetch, so the five winners' trees are never built and every metric cell reads `N/A`.

**Files:**
- Modify: `sra.py` (new command + parser registration ~line 1395)
- Test: `tests/test_prefetch_peers.py` (create)

**Interfaces:**
- Consumes: `run_prefetch`, `dependency_map`, `FETCHERS` from `lib.fetchers.registry`; `init_state`, `load_state` from `lib.statefile`; `TICKER_SUBDIRS`, `ticker_dir`, `valid_ticker` from `sra.py`.
- Produces:
  - `PEER_METRIC_KINDS: list[str]` in `sra.py`
  - `selected_peer_symbols(ticker_dir: Path) -> tuple[list[str], str | None]`
  - `cmd_prefetch_peers(args: argparse.Namespace) -> int`
  - CLI: `uv run python sra.py prefetch-peers TICKER [--stale-only] [--force-lock] [--data-root P]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prefetch_peers.py`:

```python
"""prefetch-peers builds metric bronze for the selected comparables."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sra


def _subject(tmp_path: Path, peers: list[str] | None) -> Path:
    data_root = tmp_path / "data"
    d = data_root / "SUBJ"
    (d / "derived" / "peers").mkdir(parents=True)
    (d / "structured").mkdir(parents=True)
    if peers is not None:
        (d / "derived" / "peers" / "peers_selected.json").write_text(json.dumps({
            "_meta": {"id": "peers_selected", "ticker": "SUBJ", "producer": "model"},
            "data": {"peers": [{"symbol": s, "is_subject": False} for s in peers]},
        }), encoding="utf-8")
    return data_root


def test_missing_selection_exits_1(tmp_path: Path, capsys) -> None:
    data_root = _subject(tmp_path, None)
    args = sra.build_parser().parse_args(
        ["prefetch-peers", "SUBJ", "--data-root", str(data_root)])

    assert args.fn(args) == 1
    assert "peers-select" in capsys.readouterr().err


def test_symbols_exclude_the_subject(tmp_path: Path) -> None:
    data_root = _subject(tmp_path, ["BA", "LMT"])
    d = data_root / "SUBJ"
    payload = json.loads((d / "derived" / "peers" / "peers_selected.json")
                         .read_text(encoding="utf-8"))
    payload["data"]["peers"].append({"symbol": "SUBJ", "is_subject": True})
    (d / "derived" / "peers" / "peers_selected.json").write_text(
        json.dumps(payload), encoding="utf-8")

    symbols, error = sra.selected_peer_symbols(d)

    assert error is None
    assert symbols == ["BA", "LMT"]


def test_peer_metric_kinds_are_the_four_the_table_needs() -> None:
    assert sra.PEER_METRIC_KINDS == ["profile", "prices", "financials", "technical"]


def test_run_creates_each_peer_tree_and_reports_per_peer(
        tmp_path: Path, monkeypatch, capsys) -> None:
    data_root = _subject(tmp_path, ["BA", "LMT"])
    calls: list[str] = []

    def fake_run_prefetch(ticker, ticker_dir, state, kinds, fetchers, deps, **kw):
        calls.append(ticker)
        return {"fetched": list(kinds), "skipped": [], "errors": {}, "warnings": {}}

    monkeypatch.setattr(sra, "run_prefetch", fake_run_prefetch)
    args = sra.build_parser().parse_args(
        ["prefetch-peers", "SUBJ", "--data-root", str(data_root)])

    assert args.fn(args) == 0
    assert calls == ["BA", "LMT"]
    assert (data_root / "BA" / ".state.json").exists()

    out = json.loads(capsys.readouterr().out)
    assert sorted(out["peers"]) == ["BA", "LMT"]


def test_failing_peer_is_a_warning_not_a_failure(
        tmp_path: Path, monkeypatch, capsys) -> None:
    data_root = _subject(tmp_path, ["BA", "LMT"])

    def fake_run_prefetch(ticker, ticker_dir, state, kinds, fetchers, deps, **kw):
        if ticker == "BA":
            return {"fetched": [], "skipped": [],
                    "errors": {"profile": "boom"}, "warnings": {}}
        return {"fetched": list(kinds), "skipped": [], "errors": {}, "warnings": {}}

    monkeypatch.setattr(sra, "run_prefetch", fake_run_prefetch)
    args = sra.build_parser().parse_args(
        ["prefetch-peers", "SUBJ", "--data-root", str(data_root)])

    assert args.fn(args) == 0
    assert any("BA" in w for w in json.loads(capsys.readouterr().out)["warnings"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_prefetch_peers.py -v`
Expected: FAIL — `argparse` error, "invalid choice: 'prefetch-peers'"

- [ ] **Step 3: Implement the command**

Add to `sra.py`, after `cmd_prefetch_macro`:

```python
# The four registry kinds `assemble._peer_row` reads. `technical` DEPENDS_ON
# `prices`, so prices is here to satisfy it rather than for its own artifact.
# Everything else — filings, transcripts, news, estimates — is subject-only
# work: the peer table compares four reported numbers, not a thesis.
PEER_METRIC_KINDS: list[str] = ["profile", "prices", "financials", "technical"]


def selected_peer_symbols(ticker_dir: Path) -> tuple[list[str], str | None]:
    """`(symbols, error)` for the selected comparables (§13.6).

    The subject appears in `peers_selected.json` flagged `is_subject`; it is
    dropped here because its own bronze is what `prefetch` already gathered.
    """
    path = ticker_dir / "derived" / "peers" / "peers_selected.json"
    if not path.exists():
        return [], (f"no peers_selected.json at {path} "
                    f"(run: sra.py peers-select {ticker_dir.name})")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [], f"malformed peers_selected.json: {exc}"
    entries = (payload.get("data") or {}).get("peers") or []
    symbols = [str(e.get("symbol") or "").strip().upper()
               for e in entries
               if isinstance(e, dict) and not e.get("is_subject")]
    return [s for s in symbols if s], None


def _ensure_peer_tree(data_root: Path, symbol: str) -> tuple[Path, dict] | None:
    """Create a peer's §4 tree if absent and return `(dir, state)`, or None if
    the symbol is not a legal ticker."""
    if not valid_ticker(symbol):
        return None
    d = ticker_dir(data_root, symbol)
    for sub in TICKER_SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    try:
        state = load_state(d)
    except FileNotFoundError:
        init_state(d, symbol.upper())
        state = load_state(d)
    return d, state


def cmd_prefetch_peers(args: argparse.Namespace) -> int:
    """Fetch the peer-table metrics for each selected comparable (§13.6).

    Exit 1 when the subject is not initialized or has no selection; otherwise
    0. A peer that fails is a WARNING: four good comparables beat a build that
    refuses to finish because one provider was down.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    symbols, error = selected_peer_symbols(d)
    if error is not None:
        print(f"prefetch-peers: {error}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    out: dict = {"ticker": ticker, "peers": [], "skipped": [], "warnings": []}

    for symbol in symbols:
        prepared = _ensure_peer_tree(args.data_root, symbol)
        if prepared is None:
            out["warnings"].append(f"{symbol}: not a valid ticker; skipped")
            continue
        peer_dir, state = prepared

        if args.stale_only:
            due = _due_kinds(state, PEER_METRIC_KINDS, now, peer_dir)
            kinds = [k for k in PEER_METRIC_KINDS if k in due]
        else:
            kinds = list(PEER_METRIC_KINDS)
        if not kinds:
            out["skipped"].append(symbol)
            continue

        try:
            with TickerLock(peer_dir, "prefetch-peers", force=args.force_lock):
                result = run_prefetch(symbol, peer_dir, state, kinds, FETCHERS,
                                      dependency_map(kinds))
        except LockHeldError as exc:
            out["warnings"].append(f"{symbol}: {exc}")
            continue

        out["peers"].append(symbol)
        for kind, message in (result.get("errors") or {}).items():
            out["warnings"].append(f"{symbol}/{kind}: {message}")

    print(json.dumps(out, indent=2))
    return 0
```

Verify the imports at the top of `sra.py` already cover `init_state` and
`load_state` from `lib.statefile`; add whichever is missing.

- [ ] **Step 4: Register the subcommand**

In `build_parser()`, immediately after the `prefetch` block:

```python
    sp = add("prefetch-peers", cmd_prefetch_peers, mutating=True)
    sp.add_argument("--stale-only", action="store_true",
                    help="only fetch peers whose metric artifacts are stale "
                         "or were never fetched")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_prefetch_peers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS. `tests/test_orchestrator_skill_contracts.py` may assert the set
of CLI commands; add `prefetch-peers` there if it does.

- [ ] **Step 7: Commit**

```bash
git add sra.py tests/test_prefetch_peers.py
git commit -m "feat: prefetch-peers gathers metric bronze for selected comparables"
```

---

### Task 6: Wire prefetch-peers into the skills and warn on an empty table

A new command nothing calls fixes nothing. This task makes the cold build run it, and makes a still-empty peer table visible in `run_stats.json` rather than only in the PDF.

**Files:**
- Modify: `lib/render/assemble.py` (`_peers` ~line 394, `build_variables` ~line 465)
- Modify: `.claude/skills/sra-build/SKILL.md`, `.claude/skills/sra-update/SKILL.md`, `.claude/skills/sra-assemble/SKILL.md`
- Modify: `CLAUDE.md`
- Test: `tests/test_assemble_charts.py` (extend)

**Interfaces:**
- Consumes: `cmd_prefetch_peers` CLI from Task 5.
- Produces: `peer_table_warnings(peers: list[dict]) -> list[str]` in `lib.render.assemble`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assemble_charts.py`:

```python
from lib.render.assemble import peer_table_warnings


def test_mostly_empty_peer_table_warns() -> None:
    peers = [{"symbol": "SUBJ", "is_subject": True, "forward_pe": "18.0",
              "revenue_ttm": "$9.0B", "operating_margin": "12.0%",
              "revenue_growth": "8.0%"}]
    peers += [{"symbol": s, "is_subject": False, "forward_pe": "N/A",
               "revenue_ttm": "N/A", "operating_margin": "N/A",
               "revenue_growth": "N/A"} for s in ("BA", "LMT", "RTX")]

    warnings = peer_table_warnings(peers)

    assert len(warnings) == 1
    assert "prefetch-peers" in warnings[0]


def test_populated_peer_table_does_not_warn() -> None:
    peers = [{"symbol": s, "is_subject": s == "SUBJ", "forward_pe": "18.0",
              "revenue_ttm": "$9.0B", "operating_margin": "12.0%",
              "revenue_growth": "8.0%"} for s in ("SUBJ", "BA", "LMT")]

    assert peer_table_warnings(peers) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_assemble_charts.py -k peer_table -v`
Expected: FAIL — `ImportError: cannot import name 'peer_table_warnings'`

- [ ] **Step 3: Implement the warning**

Add to `lib/render/assemble.py`, after `_peers`:

```python
_PEER_METRIC_KEYS = ("forward_pe", "revenue_ttm", "operating_margin",
                     "revenue_growth")


def peer_table_warnings(peers: list[dict]) -> list[str]:
    """Warn when the comparables carry no fetched fundamentals (§13.6).

    An all-N/A peer table is not a data limitation, it is an unrun command —
    and it is invisible in the build log, which is why the SPCX report shipped
    with five empty rows.
    """
    comparables = [p for p in peers if not p.get("is_subject")]
    if not comparables:
        return []
    cells = [p.get(key) for p in comparables for key in _PEER_METRIC_KEYS]
    empty = sum(1 for value in cells if str(value).strip().upper() == "N/A")
    if empty * 2 <= len(cells):
        return []
    return [f"peer table: {empty} of {len(cells)} comparable metric cells read "
            f"N/A — run `sra.py prefetch-peers {peers[0].get('symbol', '')}` "
            f"before assembling"]
```

In `build_variables`, capture the list once so the warning sees the same rows
the template does. Replace `"peers": _peers(ticker_dir, company_name),` with a
local built above the return dict:

```python
    peer_rows = _peers(ticker_dir, company_name)
```

and use `"peers": peer_rows,` in the dict.

In `assemble()` (the caller, ~line 573), after `variables = build_variables(...)`:

```python
    peer_warnings = peer_table_warnings(variables["peers"])
```

and extend the `data` dict's warnings key from Task 1:

```python
        "warnings": chart_warnings + body_warnings + peer_warnings,
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_assemble_charts.py -k peer_table -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire it into the cold build**

In `.claude/skills/sra-build/SKILL.md`, add a step immediately after the
`/sra-peers` step (currently around line 102):

```markdown
## Step 2b — Peer fundamentals (Bash, deterministic)

```bash
uv run python sra.py prefetch-peers <TICKER> --stale-only
```

The peer comparison table reads each comparable's OWN bronze. Without this the
table renders every cell as N/A, which reads as a data limitation and is not
one. It must run AFTER `/sra-peers`: prefetch cannot gather the winners before
they are chosen.

**Resume:** skip when every symbol in `derived/peers/peers_selected.json` has a
`structured/key_ratios_computed.json` under its own `data/<PEER>/` tree.

Report the `warnings` list. A peer that failed is not fatal — say which.
```

- [ ] **Step 6: Wire it into update and assemble**

In `.claude/skills/sra-update/SKILL.md`, add `prefetch-peers <TICKER> --stale-only`
to the refresh path, beside the existing `prefetch --stale-only` call.

In `.claude/skills/sra-assemble/SKILL.md` Step 0, add:

```markdown
Check the peer table has data before spending a polish chain on the report:

```bash
uv run python sra.py prefetch-peers <TICKER> --stale-only
```

Assembly reports a `peer table:` warning when most comparable cells read N/A.
If that warning survives this command, say so when reporting — the table will
ship empty.
```

- [ ] **Step 7: Document the command**

In `CLAUDE.md`, add a row to the command-surface table under the `prefetch-macro` row:

```markdown
| `prefetch-peers T [--stale-only]` | metric bronze for the SELECTED comparables |
```

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS. `tests/test_orchestrator_skill_contracts.py` checks skill files
against the CLI surface; update its expectations if it fails.

- [ ] **Step 9: Commit**

```bash
git add lib/render/assemble.py CLAUDE.md .claude/skills tests/test_assemble_charts.py
git commit -m "feat: run prefetch-peers in the build and warn on an empty peer table"
```

---

### Task 7: The verdict reconciliation gate

The report headlines `Sell` at `$38.13` while its probability-weighted scenario produces `$129.81`. Extracting that second number from model-written prose is too fragile to gate on, so it moves into `verdict.json`.

**Files:**
- Create: `lib/verdict_checks.py`
- Test: `tests/test_verdict_checks.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `check_verdict(verdict: dict, valuation_md: str) -> list[str]` in `lib.verdict_checks`, plus module constants `DIVERGENCE_THRESHOLD: float` and `MIN_RECONCILIATION_WORDS: int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verdict_checks.py`:

```python
"""The verdict card may not contradict the valuation section in silence."""

from __future__ import annotations

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
    thin = "The DCF governs. The scenario frame is a cross-check only."
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
        _verdict(scenario_weighted_value=40.0, reconciliation=""),
        VALUATION_MD) == []


def test_absent_scenario_frame_passes() -> None:
    assert check_verdict(
        {"fair_value": 38.13, "scenario_weighted_value": None}, "") == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_verdict_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.verdict_checks'`

- [ ] **Step 3: Implement the module**

Create `lib/verdict_checks.py`:

```python
"""Coherence checks between the verdict card and the valuation section.

The SPCX 2026-08-12 report headlined a Sell at a $38.13 fair value beside a
$129.81 probability-weighted scenario that implies Hold. The text acknowledged
the gap and never resolved it, and nothing in the pipeline could tell: the
cross-section worklist compares numbers ACROSS sections, and this pair spans
the verdict card and one section.

Parsing the second figure out of prose is too fragile to gate on, so the
scenario numbers live in `verdict.json` and this module checks them.
"""

from __future__ import annotations

import re

# Two values for one quantity, more than this far apart, are a contradiction
# the reader will notice on the first page and must be told about.
DIVERGENCE_THRESHOLD = 0.15

# Short enough that one honest paragraph clears it; long enough that "the DCF
# governs" does not.
MIN_RECONCILIATION_WORDS = 40


def _number(value: object) -> float | None:
    """A float, or None for anything that is not one — §6.4: a value nobody
    reported is absent, not zero."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _mentions(text: str, value: float) -> bool:
    """Whether `text` names `value`, ignoring currency, commas and rounding.

    Compares digit runs so "$129.81", "129.81" and "$129.8 billion" all match
    the figure they came from.
    """
    wanted = re.sub(r"\D", "", f"{value:.2f}")
    return wanted in re.sub(r"\D", "", text)


def check_verdict(verdict: dict, valuation_md: str) -> list[str]:
    """Every coherence failure, in reading order. Empty means the card passes.

    Failures rather than a boolean: the caller turns them into findings, and a
    writer fixing them wants the list, not the verdict on the verdict.
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
                f"reconciliation does not name {label} ({value:,.2f}); a "
                f"reconciliation that omits one of the two numbers reconciles "
                f"nothing")

    if text not in valuation_md:
        failures.append(
            "reconciliation text does not appear in the valuation section — a "
            "reconciliation that lives only in JSON reaches no reader")

    return failures
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_verdict_checks.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add lib/verdict_checks.py tests/test_verdict_checks.py
git commit -m "feat: coherence gate for verdict fair value vs scenario frame"
```

---

### Task 8: Wire the verdict gate into validate and the prompts

The check from Task 7 is inert until something calls it and something writes the fields.

**Files:**
- Modify: `lib/validate.py`
- Modify: `prompts/write/valuation.md`, `prompts/polish/conclusion.md`
- Modify: `STYLE.md`
- Test: `tests/test_verdict_checks.py` (extend)

**Interfaces:**
- Consumes: `check_verdict` from Task 7.
- Produces: `_check_verdict_cards(ticker_dir: Path) -> list[Finding]` in `lib.validate`, emitting code `verdict-unreconciled`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_verdict_checks.py`:

```python
import json
from pathlib import Path

from lib.validate import validate


def test_unreconciled_verdict_fails_the_gate(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run = data_root / "TEST" / "reports" / "2026-08-12"
    (run / "sections").mkdir(parents=True)
    (run / "verdict.json").write_text(json.dumps({
        "rating": "Sell", "fair_value": 38.13,
        "scenario_weighted_value": 129.81, "reconciliation": "",
    }), encoding="utf-8")
    (run / "sections" / "valuation.md").write_text("## 6. Valuation\n", encoding="utf-8")

    findings = [f for f in validate(data_root / "TEST", data_root)
                if f.code == "verdict-unreconciled"]

    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_run_without_a_verdict_is_not_a_finding(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "TEST" / "reports" / "2026-08-12").mkdir(parents=True)

    assert [f for f in validate(data_root / "TEST", data_root)
            if f.code == "verdict-unreconciled"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_verdict_checks.py -k gate -v`
Expected: FAIL — no finding with code `verdict-unreconciled`

- [ ] **Step 3: Add the validate check**

In `lib/validate.py`, add the import:

```python
from lib.verdict_checks import check_verdict
```

and the check, beside `_check_dangling_citations`:

```python
def _check_verdict_cards(ticker_dir: Path) -> list[Finding]:
    """Each run's verdict card agrees with its own valuation section (§16.4).

    A run with no `verdict.json` is not a finding: the gate also runs mid-build,
    before the polish chain has produced one.
    """
    findings: list[Finding] = []
    for card in sorted((ticker_dir / "reports").glob("*/verdict.json")):
        rel = _rel(card, ticker_dir)
        try:
            verdict = json.loads(card.read_text(encoding="utf-8"))
        except ValueError as exc:
            findings.append(Finding("error", "verdict-unreconciled", rel,
                                    f"verdict.json is not valid JSON: {exc}"))
            continue
        if not isinstance(verdict, dict):
            findings.append(Finding("error", "verdict-unreconciled", rel,
                                    "verdict.json must be an object"))
            continue
        section = card.parent / "sections" / "valuation.md"
        valuation_md = (section.read_text(encoding="utf-8")
                        if section.exists() else "")
        findings += [Finding("error", "verdict-unreconciled", rel, problem)
                     for problem in check_verdict(verdict, valuation_md)]
    return findings
```

Register it in `validate()`:

```python
    findings += _check_verdict_cards(ticker_dir)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_verdict_checks.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Teach the valuation writer to emit the fields**

In `prompts/write/valuation.md`, add:

```markdown
## The verdict card must agree with this section

If you derive a value by more than one method, `verdict.json` carries both and
the reason one governs:

```json
{
  "fair_value": 38.13,
  "scenario_weighted_value": 129.81,
  "scenario_weighted_method": "probability-weighted 2028 EV/EBITDA, 15x bear / 28x base / 35x exit",
  "scenario_probabilities": {"bear": 0.25, "base": 0.50, "bull": 0.25},
  "reconciliation": "..."
}
```

State the governing method in the FIRST sentence of the valuation discussion,
not at its end — a reader who must reach the last paragraph to learn which
number counts has read the section twice.

`reconciliation` is required whenever the two values differ by more than 15%.
It must name both figures, run at least 40 words, and appear VERBATIM in this
section: a reconciliation that lives only in JSON reaches no reader. The gate is
`lib/verdict_checks.py` and it is fatal.

`scenario_probabilities` must sum to 1.0. A base-case probability with no stated
complement tells the reader nothing.
```

- [ ] **Step 6: Teach the conclusion writer the same contract**

In `prompts/polish/conclusion.md`, add:

```markdown
The verdict card and the valuation section state the same fair value, or they
state both values and which governs. If you carry a scenario-weighted figure
that implies a different rating than the headline, say so in the conclusion in
the same breath as the rating — never leave a reader to find the contradiction
themselves.
```

- [ ] **Step 7: Add the style rule**

In `STYLE.md`, add to the rules list:

```markdown
- **Never show two materially different values for the same quantity without
  naming which governs and why.** A DCF fair value beside a scenario-weighted
  value that implies the opposite rating is the report's most important
  sentence, not an inconsistency to leave standing.
```

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS. Existing runs under `data/` are not touched by the test suite,
so a real SPCX validate failure will not appear here.

- [ ] **Step 9: Commit**

```bash
git add lib/validate.py prompts STYLE.md tests/test_verdict_checks.py
git commit -m "feat: fail the gold gate on an unreconciled verdict"
```

---

### Task 9: Report-level length rules

`polish.md` gates each section against its own pre-polish word count. Every clarifying word must be bought with a deletion from the same section, which is why sentences compressed into fragments like "Google's from the turn of the year".

**Files:**
- Modify: `lib/hard_checks.py`
- Test: `tests/test_hard_checks_report_length.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `run_checks(text: str, rules: list, base_dir: Path, *, target: Path | None = None) -> list[str]` — new keyword-only `target`; existing positional calls unchanged
  - rules `report_not_longer_than: <baseline_dir> [<factor>]` and `not_longer_than_pct: <baseline_file> <factor>`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hard_checks_report_length.py`:

```python
"""Report-level length budget: a section may grow if the report does not."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.hard_checks import run_checks


def _dirs(tmp_path: Path, baseline: dict[str, int], current: dict[str, int]
          ) -> tuple[Path, Path]:
    base = tmp_path / "sections_prepolish"
    cur = tmp_path / "sections"
    for d, spec in ((base, baseline), (cur, current)):
        d.mkdir(parents=True)
        for name, words in spec.items():
            (d / f"{name}.md").write_text(" ".join(["w"] * words), encoding="utf-8")
    return base, cur


def test_report_within_budget_passes(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100, "b": 100}, {"a": 130, "b": 70})
    target = cur / "a.md"

    failures = run_checks(target.read_text(encoding="utf-8"),
                          ["report_not_longer_than: sections_prepolish"],
                          tmp_path, target=target)

    assert failures == []


def test_report_over_budget_fails(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100, "b": 100}, {"a": 130, "b": 100})
    target = cur / "a.md"

    failures = run_checks(target.read_text(encoding="utf-8"),
                          ["report_not_longer_than: sections_prepolish"],
                          tmp_path, target=target)

    assert len(failures) == 1
    assert "230" in failures[0] and "200" in failures[0]


def test_factor_widens_the_budget(tmp_path: Path) -> None:
    _, cur = _dirs(tmp_path, {"a": 100, "b": 100}, {"a": 130, "b": 75})
    target = cur / "a.md"

    assert run_checks(target.read_text(encoding="utf-8"),
                      ["report_not_longer_than: sections_prepolish 1.03"],
                      tmp_path, target=target) == []


def test_report_rule_without_a_target_is_a_clear_failure(tmp_path: Path) -> None:
    failures = run_checks("words", ["report_not_longer_than: sections_prepolish"],
                          tmp_path)

    assert len(failures) == 1
    assert "target" in failures[0]


def test_section_pct_allows_bounded_growth(tmp_path: Path) -> None:
    base, cur = _dirs(tmp_path, {"a": 100}, {"a": 108})
    text = (cur / "a.md").read_text(encoding="utf-8")

    assert run_checks(text, ["not_longer_than_pct: sections_prepolish/a.md 1.10"],
                      tmp_path) == []


def test_section_pct_rejects_growth_past_the_factor(tmp_path: Path) -> None:
    base, cur = _dirs(tmp_path, {"a": 100}, {"a": 120})
    text = (cur / "a.md").read_text(encoding="utf-8")

    failures = run_checks(text, ["not_longer_than_pct: sections_prepolish/a.md 1.10"],
                          tmp_path)

    assert len(failures) == 1
    assert "120" in failures[0]


def test_existing_rules_still_work_without_target(tmp_path: Path) -> None:
    (tmp_path / "base.md").write_text("one two three four five", encoding="utf-8")

    assert run_checks("one two three", ["not_longer_than: base.md"], tmp_path) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_hard_checks_report_length.py -v`
Expected: FAIL — `run_checks() got an unexpected keyword argument 'target'`

- [ ] **Step 3: Implement the checks**

Add to `lib/hard_checks.py`, after `_check_not_longer_than`:

```python
def _resolve_inside(value: str, base_dir: Path) -> tuple[Path | None, str | None]:
    """A rule's path argument, resolved under `base_dir` and confined to it."""
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
    return sum(len(p.read_text(encoding="utf-8").split())
               for p in sorted(directory.glob("*.md")))


def _check_report_not_longer_than(value: str, base_dir: Path,
                                  target: Path | None) -> str | None:
    """Word count of the WHOLE report against a baseline directory (§18.3).

    The per-section gate this replaces made every clarifying word cost a
    deletion from the same section, which is how sentences compress into
    fragments. The report is the budget; a section may grow if another shrinks.
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
```

- [ ] **Step 4: Dispatch the new rules**

Change `run_checks`'s signature and add two branches:

```python
def run_checks(text: str, rules: list, base_dir: Path, *,
               target: Path | None = None) -> list[str]:
```

Add to the dispatch chain, after the `not_longer_than` branch:

```python
        elif name == "report_not_longer_than":
            problem = _check_report_not_longer_than(value, base_dir, target)
        elif name == "not_longer_than_pct":
            problem = _check_not_longer_than_pct(value, text, base_dir)
```

Update the module docstring's rule list and, in `main()`, pass the file being
checked through:

```python
    failures = run_checks(text, rules, base_dir, target=path)
```

(where `path` is `main`'s already-resolved file argument — check its local name
and use it verbatim.)

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_hard_checks_report_length.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS — the `target` parameter is keyword-only with a default, so every
existing call site is unaffected.

- [ ] **Step 7: Commit**

```bash
git add lib/hard_checks.py tests/test_hard_checks_report_length.py
git commit -m "feat: report-level length budget so clarity can cost words"
```

---

### Task 10: The clarity pass

No stage in the pipeline reads the assembled report — `critique.md` and `evaluate.md` both read `{sections_dir}`. That is why nothing saw the tripled Sankey, and why cross-section referent ambiguity ("That backlog" — which company?) survives every gate.

**Files:**
- Create: `prompts/polish/clarity.md`
- Modify: `prompts/polish/polish.md` (the gate)
- Modify: `.claude/skills/sra-assemble/SKILL.md`
- Modify: `workflows/polish_chain.js`

**Interfaces:**
- Consumes: `report_not_longer_than` and `not_longer_than_pct` from Task 9.
- Produces: `prompts/polish/clarity.md` with placeholders `{ticker}`, `{report_path}`, `{sections_dir}`, `{clarity_path}`, `{baseline_dir}`, `{section_ids}` — the same placeholder convention `prompts/polish/critique.md` uses.

- [ ] **Step 1: Swap the polish gate**

In `prompts/polish/polish.md`, replace the `## The gate` command block with:

```markdown
**This pass may not grow the REPORT.** A section may grow by up to 10% if
another shrinks to pay for it. Both rules are checked:

```bash
uv run python -m lib.hard_checks {sections_dir}/<section>.md \
    --rules-json '["report_not_longer_than: {baseline_dir}",
                   "not_longer_than_pct: {baseline_dir}/<section>.md 1.10"]'
```

The report-level rule counts WORDS across every section against the pre-polish
copies. It exists because a previous generation of this pipeline ran a polish
pass that GREW the body by 1,933 bytes while leaving every flagged redundancy in
place, and nothing detected it.

The per-section rule replaced a hard per-section ceiling, which had its own
failure mode: every clarifying word had to be bought with a deletion from the
same paragraph, and sentences compressed into fragments that parse as nothing.
"Spot pays 320.8x trailing EBITDA for AI cloud revenue that either party can
cancel on 90 days' notice, Google's from the turn of the year" shipped that way.
```

Update the verification block at the end of the same file to use both rules.

- [ ] **Step 2: Write the clarity prompt**

Create `prompts/polish/clarity.md`:

```markdown
# Clarity — the assembled-report read

You are the first reader of this report. Every stage before you saw one section
at a time; you are reading `{report_path}`, the assembled document, the way the
recipient will.

You are not scoring it and you are not restyling it. You are producing a patch
list: passages that a careful reader cannot parse, cannot resolve, or would have
to take on faith.

## What to hunt

Read the whole report. For each item below, quote the span and write the
replacement — not a description of the replacement.

**1. Fragments that parse as nothing.** A clause with no verb, or an elliptical
construction whose omitted words the reader cannot recover. Real example:
"…cancel on 90 days' notice, Google's from the turn of the year." Two dates
belong to two counterparties; write them as two clauses.

**2. Ambiguous referents.** "That backlog", "the company", "this multiple" where
two candidates precede it. Name the company, the number, or the section.

**3. Ambiguous nouns.** "The BryceTech shares above" means market shares, and
reads as equity. Rewrite the noun.

**4. Derivations asserted, not shown.** A conclusion that does not follow from
the numbers given: why a six-month booking validates multi-year capex, or a 2028
multiple. Either supply the missing step or delete the claim.

**5. Category errors.** Two different concepts treated as one — accounting
depreciation life used as cash payback, a disclosed rate compared against a
derived rate over a different denominator. Name the two concepts and say which
the argument needs.

**6. Arithmetic without a stated basis.** Excluding a segment from a market size
and then comparing revenue against the remainder needs a reason, or it needs to
go.

**7. Prose that contradicts its own numbers.** "Decelerating" beside a rising
series. Say which quantity decelerates and over what span.

**8. The governing method disclosed late.** If the rating rests on the DCF and
the EBITDA frame implies otherwise, the reader learns that in the section's
first sentence, not its last.

**9. Inconsistent conventions.** Netting cash before compounding in one place
and after in another. Print the formula once and use it everywhere.

**10. Repeated exhibits or tables.** The same chart or table appearing twice.
The assembler gates this now, so an instance here means a gate was bypassed —
report it and name the file.

## Budget

**At most 12 items, most damaging first.** Each names the section file under
`{sections_dir}`, quotes the span verbatim, and supplies replacement text.
"Section 5 is confusing" is not an item.

The fix pass has a **3% report-level word budget for all 12 items combined**. An
item whose fix needs more than its share should say so, so the fixer can trade.

## Output

Write the worklist to `{clarity_path}`. Return, as your final message, the item
count and the single passage you consider least readable.
```

- [ ] **Step 3: Wire it into the assemble skill**

In `.claude/skills/sra-assemble/SKILL.md`, insert a step between Step 3
(Assemble) and Step 4 (Validate), and renumber the steps that follow:

```markdown
## Step 3b — Clarity pass over the assembled report

This is the only stage that reads the finished document. Snapshot first:

```bash
cp -R {run_dir}/sections {run_dir}/sections_preclarity
```

1. Dispatch one agent with `prompts/polish/clarity.md`, filling `{report_path}`
   with `{run_dir}/report.md` and `{clarity_path}` with `{run_dir}/clarity.md`.

2. Dispatch one fixer to apply that worklist to the section files, under:

```bash
uv run python -m lib.hard_checks {run_dir}/sections/<section>.md \
    --rules-json '["report_not_longer_than: sections_preclarity 1.03"]'
```

   This is the one pass permitted to GROW the report, because every item it
   applies is an explanation the reader needed and did not get. 3% is the whole
   budget for all items; a fixer that cannot fit one must declare the skip.

3. **Re-assemble.** The fixes landed in section files, not in `report.md`:

```bash
uv run python sra.py assemble <TICKER>
```

Skipping the re-assemble ships the unfixed report with a clarity worklist
sitting beside it.
```

- [ ] **Step 4: Wire it into the workflow**

The clarity pass runs AFTER assembly, so it is **not** a `polish_chain.js` stage —
that chain ends before `sra.py assemble` runs, and `/sra-assemble` orchestrates
clarity itself (Step 3 above). The workflow needs only its description updated
to match the gate polish now enforces.

In `workflows/polish_chain.js`, change `meta.description` from
`'…, critique, shrink-gated polish, evaluation (spec §15.2)'` to
`'…, critique, budget-gated polish, evaluation (spec §15.2)'`, and the `Polish`
phase `detail` from `'apply the worklists under a word-count shrink gate'` to
`'apply the worklists under a report-level word budget'`.

`{baseline_dir}` is already substituted as an absolute path
(`polish_chain.js:45,141`), which is what `lib.hard_checks` needs — its
`--base-dir` defaults to the draft's own directory, so a relative baseline would
resolve under `sections/` and miss.

- [ ] **Step 5: Verify the workflow still parses**

Run: `uv run pytest tests/test_workflows_static.py -q`
Expected: PASS — this test checks the workflow scripts' structure.

- [ ] **Step 6: Verify the skill contracts still hold**

Run: `uv run pytest tests/test_assemble_skill_contract.py tests/test_orchestrator_skill_contracts.py -q`
Expected: PASS. These assert skill files reference real CLI commands and real
prompt files; `prompts/polish/clarity.md` now exists, so both should pass.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add prompts/polish .claude/skills/sra-assemble/SKILL.md workflows/polish_chain.js
git commit -m "feat: clarity pass over the assembled report, with a growth budget"
```

---

### Task 11: Voice

The report calls its own author "the analyst" in three template strings.

**Files:**
- Modify: `templates/final_report.md.j2` (3 strings)
- Modify: `STYLE.md`
- Modify: `sections.yaml` (7 rule lists)
- Modify: `prompts/polish/evaluate.md:24`
- Test: `tests/test_assemble_charts.py` (extend)

**Interfaces:**
- Consumes: `run_checks` from `lib.hard_checks` (unchanged signature for this use).
- Produces: nothing new; adds `not_regex: (?i)\bthe analyst\b` to every section rule list.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assemble_charts.py`:

```python
from lib.hard_checks import run_checks
from lib.sections import load_sections


def test_every_section_forbids_calling_the_author_the_analyst() -> None:
    rule = r"not_regex: (?i)\bthe analyst\b"
    for section_id, config in load_sections()["sections"].items():
        assert rule in config["hard_checks"], f"{section_id} is missing the voice rule"


def test_the_voice_rule_catches_the_phrase(tmp_path: Path) -> None:
    failures = run_checks("These are the analyst's own assumptions.",
                          [r"not_regex: (?i)\bthe analyst\b"], tmp_path)

    assert len(failures) == 1


def test_the_template_does_not_call_the_author_the_analyst() -> None:
    text = Path("templates/final_report.md.j2").read_text(encoding="utf-8")

    assert "the analyst" not in text.lower()
```

The key is `hard_checks` — `lib/sections.py:18` lists it among the required
per-section keys.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_assemble_charts.py -k analyst -v`
Expected: FAIL — the voice rule is in no section, and the template contains
"the analyst" three times.

- [ ] **Step 3: Fix the template**

In `templates/final_report.md.j2`, replace all three occurrences:

- `*Rating, fair value and probabilities are the analyst's own assumptions, not company guidance or`
  → `*Rating, fair value and probabilities are the report writer's own assumptions, not company guidance or`
- `<span class="caption">Sell-side consensus, not the analyst's own fair value. See the Investment Verdict for that.</span>`
  → `<span class="caption">Sell-side consensus, not the report writer's own fair value. See the Investment Verdict for that.</span>`
- `- **Assumptions** are the analyst's own, including the fair value and any scenario probabilities.`
  → `- **Assumptions** are the report writer's own, including the fair value and any scenario probabilities.`

- [ ] **Step 4: Add the rule to all seven sections**

In `sections.yaml`, add to each of the seven `rules` lists, beside the existing
`- 'not_regex: ^## [\s\S]*?^## '` line:

```yaml
      - 'not_regex: (?i)\bthe analyst\b'
```

- [ ] **Step 5: Add the style rule**

In `STYLE.md`, add:

```markdown
- **The report refers to its author as "the report writer", never "the
  analyst".** Guidance elsewhere in this file about writing *like* a sell-side
  analyst describes voice, not identity — keep it.
```

- [ ] **Step 6: Fix the evaluate typo**

In `prompts/polish/evaluate.md:24`, change:

`**2. Completeness.** Does the report cover what a analyst deciding on this`

to:

`**2. Completeness.** Does the report cover what an investor deciding on this`

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_assemble_charts.py -k analyst -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add templates/final_report.md.j2 sections.yaml STYLE.md prompts/polish/evaluate.md tests/test_assemble_charts.py
git commit -m "fix: the report calls its author the report writer"
```

---

### Task 12: End-to-end verification against SPCX

Every prior task proved itself on fixtures. This runs the real corpus.

**Files:**
- No source changes expected. Fixes land in whichever task's code the run exposes.

**Interfaces:**
- Consumes: everything from Tasks 1-11.
- Produces: a verification record appended to this plan.

- [ ] **Step 1: Confirm the suite is green**

Run: `uv run pytest -q -m "not integration"`
Expected: PASS, no skips other than integration-marked tests.

- [ ] **Step 2: Fetch the peer fundamentals**

Run: `uv run python sra.py prefetch-peers SPCX`
Expected: exit 0, `"peers": ["BA", "LMT", "RTX", "NOC", "GD"]` (or whichever
five are selected), and `data/BA/structured/key_ratios_computed.json` on disk.

This hits live providers. If it fails on network or credentials, record the
`warnings` list and continue — the remaining steps do not depend on it.

- [ ] **Step 3: Re-assemble**

Run: `uv run python sra.py assemble SPCX`
Expected: exit 0. Read the `warnings` key in the output — it should no longer
report an empty peer table.

- [ ] **Step 4: Verify each fix in the output**

```bash
grep -c "income_sankey.png" data/SPCX/reports/latest/report.md   # expect 1
grep -c "price_weekly.png"  data/SPCX/reports/latest/report.md   # expect 1
grep -c '\[\^'              data/SPCX/reports/latest/report.md   # expect 0
grep -c 'href="#ref-'       data/SPCX/reports/latest/report.html # expect > 0
grep -c "the analyst"       data/SPCX/reports/latest/report.md   # expect 0
grep -c '\$N/A'             data/SPCX/reports/latest/report.md   # expect 0
```

Any count that disagrees is a defect in the task that owns it — go fix that
task's code and its test, not the report.

- [ ] **Step 5: Run the gate and read the expected failure**

Run: `uv run python sra.py validate SPCX`
Expected: **exit non-zero**, with one or more `verdict-unreconciled` findings.
SPCX's `verdict.json` has no `scenario_weighted_value` and no `reconciliation`,
and the report headlines Sell at $38.13 beside a $129.81 scenario. The gate
firing is the pass condition for Task 8.

There must be NO `exhibit-duplicated` and NO `citation-unlinked` findings — those
would mean Tasks 1-4 did not hold on real data.

- [ ] **Step 6: Record the result**

Append a `## Verification` section to this plan file with the six grep counts,
the validate exit code, and the finding codes observed.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/plans/2026-08-12-report-quality.md data/SPCX
git commit -m "chore: verify report quality fixes against the SPCX corpus"
```

---

## Out of scope

A cold rebuild of SPCX. Task 10's prompt changes only exercise in a full run,
and fixing SPCX's *content* (supplying the reconciliation the gate now demands,
rewriting the ten unclear passages) is editorial work on one report, not
pipeline work. Both are follow-up tasks.
