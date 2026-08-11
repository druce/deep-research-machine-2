"""Wiki page primitives and the ADVISORY wiki lint (spec §4, §20, §22.1).

The wiki is silver: synthesized research knowledge, never a citation target
(§8.1). These are the mechanical primitives — page IO, the generated index, the
append-only log — plus `wiki_lint`, the deterministic half of §22's quality
controls.

`wiki_lint` is advisory and never fails a build. That separation is
deliberate: the fatal gate (`validate`, §8.4) checks facts about provenance
that are always defects, while these checks are prose-quality signals with real
false-positive rates. Wiring them into the fatal gate would either block builds
on style or train people to ignore the gate that actually matters. Citation
RESOLUTION lives in `validate`, not here.

Model judgment stays out of this module entirely (§22.1): whether a cited
source actually supports a claim, and whether an analytical tension is genuine,
are the only two questions left to `/sra-lint`, and both run after this.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from lib.provenance import resolve_artifact
from lib.sections import SECTION_IDS
from lib.validate import Finding

WIKI_SUBDIR = "wiki"
INDEX_NAME = "00_index.md"
LOG_NAME = "log.md"
_BOOKKEEPING = (INDEX_NAME, LOG_NAME)

CITATION_RE = re.compile(r"\[\^([A-Za-z0-9][\w.\-]*)\]")

# A "numeric claim" (§22.1). Deliberately NOT any digit: a bare year would make
# every sentence mentioning a date demand a citation, and a check that fires
# constantly is one people learn to skip. Currency, percentages, thousands-
# separated numbers and decimals are what a claim is actually made of.
NUMERIC_CLAIM_RE = re.compile(
    r"\$\s*\d[\d,.]*"          # $1.2, $ 400
    r"|\d[\d,.]*\s*%"          # 15.4%
    r"|\b\d{1,3}(?:,\d{3})+\b"  # 1,234,567
    r"|\b\d+\.\d+\b"           # 15.4
)

# Forward-looking references (§22.1): fiscal years, quarters, and calendar
# years from 2026 on.
FORWARD_LOOKING_RE = re.compile(r"FY\d{2}|Q[1-4]\b|\b20(?:2[6-9]|3\d)\b")
STATUS_TAG_RE = re.compile(r"\[(REPORTED|GUIDANCE|CONSENSUS|ESTIMATE)\]")

# A figure for duplicate detection: the number together with its unit, so "15.4%"
# and "15.4x" are different facts.
FIGURE_RE = re.compile(r"\$\s*\d[\d,.]*[a-zA-Z]*|\d[\d,.]*\s*(?:%|x\b|bp\b)")

# Rows of the ownership table inside sections.yaml's `section_ownership` prose.
_OWNERSHIP_ROW_RE = re.compile(r"^\|\s*(?P<facts>[^|]+?)\s*\|\s*(?P<owner>§\d[^|]*?)\s*\|\s*$")


# --- page IO --------------------------------------------------------------

def page_path(ticker_dir: Path, page: str) -> Path:
    return ticker_dir / WIKI_SUBDIR / f"{page}.md"


def read_page(ticker_dir: Path, page: str) -> tuple[dict, str]:
    path = page_path(ticker_dir, page)
    if not path.exists():
        raise FileNotFoundError(path)
    post = frontmatter.load(path)
    return dict(post.metadata), post.content


def write_page(ticker_dir: Path, page: str, meta: dict, body: str,
               now: datetime | None = None) -> Path:
    """Write a wiki page, defaulting the frontmatter §20 requires.

    `updated_at` is always stamped fresh — a page's recorded time must reflect
    this write, not whatever the caller happened to pass through. `built_from`
    and `open_questions` default to empty so consumers can read them without
    guarding, and `built_from` entries stay as the caller gave them: they are
    stamped references (§7), and rewriting them would erase the timestamp
    `invalidate` reads.
    """
    path = page_path(ticker_dir, page)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = dict(meta)
    fm.setdefault("section", page)
    fm["updated_at"] = (now or datetime.now(timezone.utc)).isoformat()
    fm.setdefault("built_from", [])
    fm.setdefault("open_questions", [])
    post = frontmatter.Post(body, **fm)
    path.write_text(frontmatter.dumps(post, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _pages(ticker_dir: Path) -> list[Path]:
    """Every real wiki page — the generated index and the log are bookkeeping,
    not knowledge."""
    wiki = ticker_dir / WIKI_SUBDIR
    if not wiki.is_dir():
        return []
    return [p for p in sorted(wiki.rglob("*.md")) if p.name not in _BOOKKEEPING]


def _page_name(ticker_dir: Path, path: Path) -> str:
    return str(path.relative_to(ticker_dir / WIKI_SUBDIR).with_suffix(""))


def update_index(ticker_dir: Path) -> Path:
    """Regenerate `wiki/00_index.md` from page frontmatter.

    Deterministic and idempotent, like the source manifest: the index is
    generated, never hand-edited, so regenerating must not show up as a change.
    """
    wiki = ticker_dir / WIKI_SUBDIR
    wiki.mkdir(parents=True, exist_ok=True)
    lines = [f"# Wiki index — {ticker_dir.name}", "",
             "| Page | Section | Summary | Updated | Sources | Open Qs |",
             "|---|---|---|---|---:|---:|"]
    rows = 0
    for path in _pages(ticker_dir):
        post = frontmatter.load(path)
        body_lines = [ln.strip() for ln in post.content.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
        summary = body_lines[0][:120].replace("|", r"\|") if body_lines else ""
        lines.append(
            f"| {_page_name(ticker_dir, path)} | {post.metadata.get('section', '')} | "
            f"{summary} | {str(post.metadata.get('updated_at', ''))[:10]} | "
            f"{len(post.metadata.get('built_from') or [])} | "
            f"{len(post.metadata.get('open_questions') or [])} |"
        )
        rows += 1
    if not rows:
        lines.append("| (no pages yet) | | | | | |")
    out = wiki / INDEX_NAME
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def append_log(ticker_dir: Path, entry: str, now: datetime | None = None) -> Path:
    """Append one timestamped line to `wiki/log.md`, the append-only journal."""
    log = ticker_dir / WIKI_SUBDIR / LOG_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with log.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {entry}\n")
    return log


# --- advisory lint (§22.1) ------------------------------------------------

def ownership_map(sections_cfg: dict) -> dict[str, str]:
    """Fact-class phrase -> owning section id, parsed from the ownership table
    inside `sections.yaml`'s `section_ownership` prose.

    That prose is the single source of truth writers are given (§18), so the
    check reads it rather than keeping a second copy that could disagree with
    what the writers were told. Table rows are `| fact classes | §N Name |`,
    and §N maps positionally onto `SECTION_IDS`, whose order the sections
    loader already enforces.

    Only MULTI-WORD phrases become keywords. Single words from these lists
    ("history", "risks", "capacity") are ordinary English that appears in every
    section's prose; matching them would bury the real breaches in noise.
    """
    mapping: dict[str, str] = {}
    for line in (sections_cfg.get("section_ownership") or "").splitlines():
        match = _OWNERSHIP_ROW_RE.match(line.strip())
        if not match:
            continue
        owner = match.group("owner")
        index_match = re.match(r"§(\d)", owner)
        if not index_match:
            continue
        index = int(index_match.group(1)) - 1
        if not 0 <= index < len(SECTION_IDS):
            continue
        for phrase in match.group("facts").split(","):
            phrase = phrase.strip().lower()
            if len(phrase.split()) >= 2:
                mapping[phrase] = SECTION_IDS[index]
    return mapping


def _prose(text: str) -> str:
    """Text with citation markers removed.

    A citation id is metadata, not a claim, and bronze ids are date-prefixed
    (`2026-07-30_news_yahoo`) — leaving them in makes every cited sentence look
    like it contains a forward-looking year, so the better a page is cited the
    more it would be flagged.
    """
    return CITATION_RE.sub(" ", text)


def _paragraphs(body: str) -> list[str]:
    """Blank-line-separated paragraphs, skipping headings and table rows —
    neither carries prose claims, and a table of figures would otherwise
    trip every numeric check on the page."""
    out = []
    for block in re.split(r"\n\s*\n", body):
        lines = [ln for ln in block.splitlines()
                 if ln.strip() and not ln.lstrip().startswith(("#", "|"))]
        if lines:
            out.append("\n".join(lines))
    return out


def _lint_page(ticker_dir: Path, path: Path, page: str, section: str,
               owners: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    post = frontmatter.load(path)
    rel = str(path.relative_to(ticker_dir))

    for paragraph in _paragraphs(post.content):
        has_citation = bool(CITATION_RE.search(paragraph))
        paragraph = _prose(paragraph)
        numbers = NUMERIC_CLAIM_RE.findall(paragraph)

        if numbers and not has_citation:
            # Scanned per paragraph, not per page: a citation three paragraphs
            # away does not support this number, and page-level scanning would
            # let one citation launder every figure on the page.
            findings.append(Finding(
                "warning", "uncited-number", rel,
                f"paragraph states a number but carries no [^id] citation: "
                f"{paragraph.strip()[:100]!r}",
            ))

        if (FORWARD_LOOKING_RE.search(paragraph) and numbers
                and not STATUS_TAG_RE.search(paragraph)):
            findings.append(Finding(
                "warning", "untagged-forward-number", rel,
                "forward-looking number without a [REPORTED]/[GUIDANCE]/"
                "[CONSENSUS]/[ESTIMATE] status tag",
            ))

    lowered = _prose(post.content).lower()
    for phrase, owner in owners.items():
        if owner != section and re.search(rf"\b{re.escape(phrase)}\b", lowered):
            findings.append(Finding(
                "warning", "section-ownership", rel,
                f"mentions {phrase!r}, a fact class owned by section {owner!r} "
                f"(§18): reference it without restating the number",
            ))

    for ref in post.metadata.get("built_from") or []:
        ref_id = ref.get("id") if isinstance(ref, dict) else ref
        if isinstance(ref_id, str) and resolve_artifact(ticker_dir, ref_id) is None:
            findings.append(Finding(
                "warning", "invalid-built-from", rel,
                f"built_from id {ref_id!r} resolves to no artifact",
            ))

    _ = page  # page name is carried by `rel`; kept for signature symmetry
    return findings


def _duplicate_figures(ticker_dir: Path) -> list[Finding]:
    """The same figure stated on two different pages (§18's ownership rule:
    state an owned fact in full exactly once). Repetition WITHIN one page is
    not a breach — that is a page referring to its own number."""
    pages_by_figure: dict[str, set[str]] = {}
    for path in _pages(ticker_dir):
        page = _page_name(ticker_dir, path)
        text = _prose(frontmatter.load(path).content)
        for figure in {m.group(0).replace(" ", "") for m in FIGURE_RE.finditer(text)}:
            pages_by_figure.setdefault(figure, set()).add(page)

    return [
        Finding("warning", "duplicate-figure", f"{WIKI_SUBDIR}/",
                f"figure {figure!r} appears on {len(pages)} pages "
                f"({', '.join(sorted(pages))}): §18 says state an owned fact once")
        for figure, pages in sorted(pages_by_figure.items())
        if len(pages) > 1
    ]


def _unindexed_entities(ticker_dir: Path) -> list[Finding]:
    """Entity pages absent from `00_index.md` (§22.1's set difference). The
    index is a researcher's map of the wiki; a page missing from it is a page
    nobody will read."""
    entities_dir = ticker_dir / WIKI_SUBDIR / "entities"
    if not entities_dir.is_dir():
        return []
    index_path = ticker_dir / WIKI_SUBDIR / INDEX_NAME
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    return [
        Finding("warning", "entity-not-indexed",
                str(path.relative_to(ticker_dir)),
                f"entity page {_page_name(ticker_dir, path)!r} is not listed in "
                f"{INDEX_NAME} (run: sra.py wiki-index)")
        for path in sorted(entities_dir.glob("*.md"))
        if _page_name(ticker_dir, path) not in index_text
    ]


def wiki_lint(ticker_dir: Path, sections_cfg: dict) -> list[Finding]:
    """Run every §22.1 deterministic check. ADVISORY: all findings are
    `warning`, and the CLI always exits 0."""
    owners = ownership_map(sections_cfg)
    findings: list[Finding] = []
    for path in _pages(ticker_dir):
        page = _page_name(ticker_dir, path)
        section = str(frontmatter.load(path).metadata.get("section") or page)
        findings += _lint_page(ticker_dir, path, page, section, owners)
    findings += _duplicate_figures(ticker_dir)
    findings += _unindexed_entities(ticker_dir)
    return findings
