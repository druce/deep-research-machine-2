#!/usr/bin/env python3
"""Deterministic assembly of the final report (spec §15.3).

`sra.py assemble` never launches a model agent. Everything here is arithmetic
and file concatenation over what the write wave and the polish chain already
produced.

`assemble` is the whole §15.3 sequence: recompute the verdict, validate the
chartbook, concatenate the sections, renumber the citations, write the
references and the citation map, and render markdown → HTML → PDF.

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
import re
import subprocess
import tempfile
from pathlib import Path

from lib.charts.common import number, read_artifact, read_derived
from lib.hard_checks import run_checks
from lib.references import (
    build_references_md, collect_citations, renumber, write_citation_map,
)
from lib.render.postprocess import (
    build_targets, format_market_cap, format_number, format_price,
    format_report_date, map_technical, next_earnings_date, postprocess,
    shorten_company_name,
)
from lib.sections import SECTION_IDS, load_sections

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
TEMPLATE_NAME = "final_report.md.j2"
CSS_NAME = "report.css"

VERDICT_NAME = "verdict.json"
SECTIONS_SUBDIR = "sections"
CONCLUSION_NAME = "conclusion.md"
CHARTBOOK_NAME = "chartbook.json"

# §16.2: the dashboard template places these two itself, before Section 1. A
# chartbook that also selects one produces the same image twice in one report.
DASHBOARD_CHARTS: frozenset[str] = frozenset({"price_weekly", "income_sankey"})
REFERENCES_NAME = "references.md"
RUN_STATS_NAME = "run_stats.json"
REPORT_STEM = "report"

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


# --- chartbook (§16.2) ----------------------------------------------------

def load_chartbook(ticker_dir: Path) -> tuple[list[dict], list[str], list[str]]:
    """`(exhibits, problems, warnings)` from `charts/chartbook.json`.

    An absent chartbook is not a problem: it means the selection skill has not
    run, and a report with no exhibits is worse but still a report. A chartbook
    that names an exhibit which did not render IS a problem — §15.3 refuses
    references to nonexistent chart candidates, because the alternative is a
    hole in the assembled PDF that nobody sees until a reader does.

    `problems` is fatal at the gate; `warnings` is not. A selection the
    dashboard already placed is a warning, because the chart does reach the
    reader — just not from here.

    Exhibits come back sorted by `order` and numbered 1..n, so the number a
    caption shows is the number the reader counts.
    """
    path = ticker_dir / "charts" / CHARTBOOK_NAME
    if not path.exists():
        return [], [], []
    try:
        book = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"cannot read {CHARTBOOK_NAME}: {exc}"], []
    selected = book.get("selected") if isinstance(book, dict) else None
    if not isinstance(selected, list):
        return [], [f"{CHARTBOOK_NAME}: 'selected' must be a list"], []

    exhibits: list[dict] = []
    problems: list[str] = []
    warnings: list[str] = []
    for entry in selected:
        if not isinstance(entry, dict):
            problems.append(f"{CHARTBOOK_NAME}: every selection must be an object")
            continue
        name = str(entry.get("name") or "")
        section = str(entry.get("section") or "")
        png = ticker_dir / "charts" / "candidates" / f"{name}.png"
        if not name or not png.exists():
            problems.append(
                f"{CHARTBOOK_NAME}: selection {name!r} has no rendered candidate "
                f"at charts/candidates/{name}.png")
            continue
        if section not in SECTION_IDS:
            problems.append(
                f"{CHARTBOOK_NAME}: selection {name!r} names section {section!r}, "
                f"which is not one of {', '.join(SECTION_IDS)}")
            continue
        if name in DASHBOARD_CHARTS:
            warnings.append(
                f"{CHARTBOOK_NAME}: selection {name!r} is already placed by the "
                f"dashboard; dropping it from the inline exhibits")
            continue
        exhibits.append({
            "name": name,
            "section": section,
            "order": number(entry.get("order")) or 0.0,
            "caption": str(entry.get("caption") or "").strip(),
            "png": png,
        })

    exhibits.sort(key=lambda e: e["order"])
    for n, exhibit in enumerate(exhibits, start=1):
        exhibit["number"] = n
    return exhibits, problems, warnings


def _figure(path: str, caption: str) -> str:
    """One pandoc fenced-div figure. Empty alt on purpose (§15.3)."""
    return f"::: {{.figure}}\n![]({path})\n\n<span class=\"caption\">{caption}</span>\n:::\n"


def _relative(target: Path, run_dir: Path) -> str:
    """A chart path as the report sees it. Relative so the run directory can be
    moved, copied or diffed against another run without rewriting every embed —
    and weasyprint resolves it against the HTML's own directory."""
    return os.path.relpath(target, run_dir)


# --- body assembly --------------------------------------------------------

def _read_sections(run_dir: Path) -> tuple[dict[str, str], list[str]]:
    """Every section draft by id, plus the ids that are missing.

    A missing section is fatal rather than skipped: the report is numbered
    §1..§7 and a gap renumbers the reader's mental model of what they are
    reading.
    """
    drafts: dict[str, str] = {}
    missing: list[str] = []
    for section_id in SECTION_IDS:
        path = run_dir / SECTIONS_SUBDIR / f"{section_id}.md"
        if path.exists():
            drafts[section_id] = path.read_text(encoding="utf-8")
        else:
            missing.append(section_id)
    return drafts, missing


def _body(drafts: dict[str, str], exhibits: list[dict],
          run_dir: Path) -> tuple[str, list[str]]:
    """Sections in `SECTION_IDS` order, each followed by its exhibits (§16.2).

    An image already embedded is skipped rather than repeated. The selector is
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


# --- template variables ---------------------------------------------------

def _percent(value: float | None, digits: int = 1) -> str:
    """A ratio reported as a fraction, rendered as a percentage. `None` stays
    'N/A' — §6.4: a value nobody reported is absent, not zero."""
    return "N/A" if value is None else f"{value * 100:.{digits}f}%"


def _ratio_block(ticker_dir: Path, symbol: str) -> dict:
    """One company's `key_ratios_computed` categories, empty when unbuilt."""
    ratios = read_artifact(ticker_dir.parent / symbol.upper(), "key_ratios_computed")
    return ratios or {}


def _peer_row(ticker_dir: Path, symbol: str, name: str, *, is_subject: bool) -> dict:
    ratios = _ratio_block(ticker_dir, symbol)
    valuation = ratios.get("valuation") or {}
    highlights = ratios.get("highlights") or {}
    profitability = ratios.get("profitability") or {}
    technical = read_artifact(ticker_dir.parent / symbol.upper(),
                              "technical_indicators_computed") or {}
    forward_pe = number(valuation.get("forward_pe"))
    return {
        "symbol": symbol.upper(),
        "name": shorten_company_name(name or symbol.upper()),
        "price": format_price(technical.get("close")),
        "market_cap": format_market_cap(highlights.get("market_cap")),
        "forward_pe": "N/A" if forward_pe is None else f"{forward_pe:.1f}",
        "revenue_ttm": format_market_cap(highlights.get("revenue_ttm")),
        "operating_margin": _percent(number(profitability.get("operating_margin"))),
        "revenue_growth": _percent(number(highlights.get("revenue_growth_yoy"))),
        "is_subject": is_subject,
    }


def _peers(ticker_dir: Path, company_name: str) -> list[dict]:
    """The subject followed by its selected comparables (§13.6).

    A peer whose own ticker has never been built has no bronze, so its metric
    cells read N/A rather than being filled from the subject's provider — the
    peer table is a comparison of reported figures or it is nothing.
    """
    rows = [_peer_row(ticker_dir, ticker_dir.name, company_name, is_subject=True)]
    selected = read_derived(ticker_dir, "peers", "peers_selected") or {}
    for peer in selected.get("peers") or []:
        symbol = str(peer.get("symbol") or "").strip()
        if symbol:
            rows.append(_peer_row(ticker_dir, symbol, str(peer.get("name") or ""),
                                  is_subject=False))
    return rows if len(rows) > 1 else []


def _anchor(title: str) -> str:
    """Pandoc's identifier for `## <n>. <title>`, the heading every section
    draft opens with.

    Pandoc drops everything before the first letter when it builds an id, so
    `## 1. Company Profile` becomes `#company-profile` — the number is NOT part
    of the anchor, and a table of contents that includes it links nowhere.
    """
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def build_variables(ticker_dir: Path, run_dir: Path, *, verdict: dict,
                    body: str, conclusion: str, references: str,
                    exhibits: list[dict]) -> dict:
    """Everything the template reads, from bronze plus the run's own drafts.

    Every value degrades on its own: an absent artifact leaves its block out of
    the report rather than failing the build, which is what lets a report
    assemble when one provider was down (§22.3).
    """
    profile = read_artifact(ticker_dir, "profile_yahoo") or {}
    ratios = read_artifact(ticker_dir, "key_ratios_computed") or {}
    valuation = ratios.get("valuation") or {}
    highlights = ratios.get("highlights") or {}
    technical = map_technical(read_artifact(ticker_dir, "technical_indicators_computed"))

    company_name = str(profile.get("longName") or ticker_dir.name)
    trailing_pe = number(valuation.get("trailing_pe"))
    forward_pe = number(valuation.get("forward_pe"))
    market_cap = highlights.get("market_cap") or profile.get("marketCap")

    card = dict(verdict)
    implied = number(card.get("implied_return_pct"))
    card["return_direction"] = ("flat" if implied is None else
                                "pos" if implied > 0 else
                                "neg" if implied < 0 else "flat")

    cfg = load_sections()
    price_png = ticker_dir / "charts" / "candidates" / "price_weekly.png"
    sankey_png = ticker_dir / "charts" / "candidates" / "income_sankey.png"

    return {
        "symbol": ticker_dir.name,
        "company_name": company_name,
        "company_name_short": shorten_company_name(company_name),
        "sector": profile.get("sector") or "",
        "industry": profile.get("industry") or "",
        "report_date": format_report_date(run_dir.name) or run_dir.name,
        "latest_price": technical["latest_price"] if technical else "N/A",
        "market_cap": format_market_cap(market_cap),
        "trailing_pe": "N/A" if trailing_pe is None else f"{trailing_pe:.1f}",
        "forward_pe": "N/A" if forward_pe is None else f"{forward_pe:.1f}",
        "verdict": card,
        "technical_analysis": technical,
        "peers": _peers(ticker_dir, company_name),
        "peer_caption": ("Forward P/E · revenue TTM · operating margin · "
                         "year-over-year revenue growth, each from the named "
                         "company's own reported figures. Subject shaded; a "
                         "comparable with no fetched fundamentals reads N/A."),
        "targets": build_targets(read_artifact(ticker_dir, "price_targets_yahoo")),
        "next_earnings": next_earnings_date(
            read_artifact(ticker_dir, "events_calendar_yahoo")),
        "chart_path": _relative(price_png, run_dir) if price_png.exists() else None,
        "chart_caption": ("4-year weekly price with 13- and 52-week moving "
                          "averages, volume, and relative strength vs the S&P 500"),
        "income_statement_sankey_path": (_relative(sankey_png, run_dir)
                                         if sankey_png.exists() else None),
        "sankey_caption": ("Revenue flowing through cost of revenue, operating "
                           "expense and tax to net income"),
        "toc_sections": [
            {"anchor": _anchor(cfg["sections"][sid]["title"]),
             "title": cfg["sections"][sid]["title"]}
            for sid in SECTION_IDS
        ],
        "body": body,
        "conclusion": conclusion,
        "chartbook": [
            {"name": e["name"], "number": e["number"],
             "path": _relative(e["png"], run_dir), "caption": e["caption"]}
            for e in exhibits
        ],
        "references": references,
    }


# --- run stats ------------------------------------------------------------

def _update_run_stats(run_dir: Path, block: dict) -> None:
    """Merge assembly's own block into `run_stats.json` (§23.4).

    Merge, not replace: the polish chain and the orchestrator write their own
    keys into the same file, and assembly runs last.
    """
    path = run_dir / RUN_STATS_NAME
    stats: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            stats = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            stats = {}
    stats["assemble"] = block
    _write_atomic(path, stats)


# --- the assembler --------------------------------------------------------

def assemble(ticker_dir: Path, run_dir: Path) -> tuple[bool, dict, str | None]:
    """Render one report run, deterministically (§15.3).

    Returns `(success, data, error)` — the project's data-function contract —
    where `data` names the files written. §19's table describes this as
    `assemble T`; the driver turns a `False` into exit 1.

    Order matters and is the spec's: verdict first (the card is the one number
    a reader checks against another number on the same page), then the
    chartbook (a dangling exhibit is a hole in the PDF), then the prose gates,
    then citations, then rendering. Nothing is written until every gate has
    passed, so a refused assembly leaves the previous report intact.
    """
    ok, verdict, err = recompute_verdict(run_dir)
    if not ok:
        return False, {}, f"verdict: {err}"

    exhibits, problems, chart_warnings = load_chartbook(ticker_dir)
    if problems:
        return False, {}, "; ".join(problems)

    drafts, missing = _read_sections(run_dir)
    if missing:
        return False, {}, (f"missing section draft(s): {', '.join(missing)} "
                           f"— run the write wave before assembling")
    conclusion_path = run_dir / CONCLUSION_NAME
    if not conclusion_path.exists():
        return False, {}, (f"missing {CONCLUSION_NAME} — run the polish chain "
                           f"before assembling")
    conclusion = conclusion_path.read_text(encoding="utf-8")

    # §8.2's prohibition, re-checked at the gate: a section can be hand-edited
    # after its writer passed the check, and this is the last point before the
    # prose reaches a reader. Run over the drafts, NOT over the assembled body:
    # the figure embeds this function adds carry `charts/candidates/...` paths,
    # which are markdown image targets rather than prose and would trip the
    # check on every report that has an exhibit.
    prose = "\n".join(list(drafts.values()) + [conclusion])
    failures = run_checks(prose, ["no_internal_filenames"], run_dir)
    if failures:
        return False, {}, "; ".join(failures)

    body, body_warnings = _body(drafts, exhibits, run_dir)

    ids = collect_citations(body + "\n" + conclusion)
    mapping = {artifact_id: n for n, artifact_id in enumerate(ids, start=1)}
    try:
        references = build_references_md(ticker_dir, ids)
    except ValueError as exc:
        return False, {}, str(exc)

    body = renumber(body, mapping)
    conclusion = renumber(conclusion, mapping)

    variables = build_variables(ticker_dir, run_dir, verdict=verdict, body=body,
                                conclusion=conclusion, references=references,
                                exhibits=exhibits)
    markdown = render_markdown(variables)

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / REFERENCES_NAME).write_text(references, encoding="utf-8")
    write_citation_map(run_dir, {n: artifact_id for artifact_id, n in mapping.items()})
    md_path = run_dir / f"{REPORT_STEM}.md"
    md_path.write_text(markdown, encoding="utf-8")

    html_path = run_dir / f"{REPORT_STEM}.html"
    pdf_path = run_dir / f"{REPORT_STEM}.pdf"
    pagetitle = f"{variables['company_name']} ({variables['symbol']}) Equity Research"
    render_errors = [message for message in (
        to_html(md_path, html_path, pagetitle=pagetitle),
        None if not html_path.exists() else to_pdf(html_path, pdf_path),
    ) if message]

    data = {
        "markdown": md_path,
        "html": html_path if html_path.exists() else None,
        "pdf": pdf_path if pdf_path.exists() else None,
        "references": run_dir / REFERENCES_NAME,
        "citation_map": run_dir / "citation_map.json",
        "citations": len(ids),
        "exhibits": len(exhibits),
        "warnings": chart_warnings + body_warnings,
        "render_errors": render_errors,
    }
    _update_run_stats(run_dir, {
        "run": run_dir.name,
        "sections": len(SECTION_IDS),
        "citations": len(ids),
        "exhibits": len(exhibits),
        "rendered": [fmt for fmt, path in (("md", md_path), ("html", html_path),
                                           ("pdf", pdf_path)) if path.exists()],
        # §22.3: a render failure degrades into a reported error rather than a
        # crash — the assembled markdown is on disk either way.
        "render_errors": render_errors,
    })
    return True, data, None
