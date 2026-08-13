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

from lib.manifest import cell
from lib.provenance import resolve_artifact


def _resolves_with_macro(ticker_dir: Path, ref_id: str) -> bool:
    """Ticker artifacts first, then `_MACRO` — the reach a citation already has."""
    if resolve_artifact(ticker_dir, ref_id) is not None:
        return True
    macro = ticker_dir.parent / "_MACRO"
    if macro.is_dir() and macro.resolve() != ticker_dir.resolve():
        return resolve_artifact(macro, ref_id) is not None
    return False


from lib.run_log import RUN_LOG_NAME
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
# The qualifier is optional because §18's own worked example carries one:
# "[CONSENSUS, yfinance, as of 2026-07-30]". Requiring the bare tag made the
# check fire on prose written exactly as the spec instructs — two SPCX
# synthesizers reported it, and one reworded correct prose to silence it, which
# is the worst outcome an advisory check can produce.
STATUS_TAG_RE = re.compile(
    r"\[(REPORTED|GUIDANCE|CONSENSUS|ESTIMATE)\b[^\]]*\]")

# A figure for duplicate detection: the number together with its unit, so "15.4%"
# and "15.4x" are different facts.
FIGURE_RE = re.compile(r"\$\s*\d[\d,.]*[a-zA-Z]*|\d[\d,.]*\s*(?:%|x\b|bp\b)")

# Rows of the ownership table inside sections.yaml's `section_ownership` prose.
_OWNERSHIP_ROW_RE = re.compile(r"^\|\s*(?P<facts>[^|]+?)\s*\|\s*(?P<owner>§\d[^|]*?)\s*\|\s*$")

# Openers that describe a page's ASSIGNMENT rather than its findings. Every
# PANW page begins with one, which is how the old index came to summarise seven
# pages as seven restatements of their own personas. Kept short and explicit: a
# broad heuristic here would silently swallow real first sentences.
_PREAMBLE_RE = re.compile(
    r"^(?:scope|persona|working notes|period convention|one-line frame"
    r"|fiscal (?:note|year)|frontmatter note|the one framing fact"
    r"|read this page|these are|this page)\b", re.I)


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


def mark_page_dirty(ticker_dir: Path, page: str, dirty: bool = True) -> Path:
    """Set (or clear) a wiki page's `dirty` flag in frontmatter (§10.2).

    Deliberately NOT `write_page`: that stamps `updated_at` fresh, which is
    right when the notes are rewritten and wrong here. Marking a page dirty is
    bookkeeping ABOUT the page — its evidence moved underneath it — and
    restamping would claim the notes were revised when nothing in them changed,
    which is exactly the signal a synthesizer uses to decide what to re-read.

    `built_from` is likewise left byte-for-byte alone: those stamps are what
    `invalidate` compares against next time (§7).
    """
    path = page_path(ticker_dir, page)
    if not path.exists():
        raise FileNotFoundError(path)
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    if dirty:
        post.metadata["dirty"] = True
    else:
        post.metadata.pop("dirty", None)
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


def _summary(post: "frontmatter.Post") -> str:
    """One line describing what a page establishes.

    An explicit `summary:` in frontmatter wins, and is what §14.2 asks a
    synthesizer to write. The fallback exists for pages written before that
    contract and is deliberately conservative: it strips the machinery a
    working note carries (citations, status tags, emphasis) and skips the
    scope/persona preamble most pages open with, because "Persona frame:
    market structure and Porter's five forces" describes the assignment
    rather than the findings — and an index of assignments is no map at all.
    """
    declared = post.metadata.get("summary")
    if isinstance(declared, str) and declared.strip():
        return _shorten(declared.strip())

    for block in re.split(r"\n\s*\n", post.content):
        text = " ".join(ln.strip() for ln in block.splitlines()
                        if ln.strip()
                        and not ln.lstrip().startswith(("#", "|", "-", ">"))
                        and not re.match(r"^\d+[.)]\s", ln.lstrip()))
        if not text:
            continue
        text = STATUS_TAG_RE.sub("", _prose(text))
        text = re.sub(r"\*\*|__|(?<!\w)[*_](?!\s)", "", text)
        text = re.sub(r"\s+", " ", text)
        # Removing a citation mid-sentence leaves the space in front of it, so
        # "…filed 2025-08-29[^id]." reads back as "…filed 2025-08-29 .".
        text = re.sub(r"\s+([.,;:)])", r"\1", text).strip()
        # Drop leading preamble SENTENCES rather than the whole paragraph. Most
        # pages open "**One-line frame.** PANW owns none of its physical supply
        # chain" — skipping the block would throw away the one good sentence on
        # the page along with the label in front of it.
        while text and _PREAMBLE_RE.match(text):
            parts = re.split(r"(?<=[.!?:])\s+", text, maxsplit=1)
            if len(parts) < 2:
                text = ""
                break
            text = parts[1].strip()
        # Gate what will actually be DISPLAYED, not the paragraph it came
        # from: a long paragraph opening with a four-word sentence would
        # otherwise pass the check and then be truncated back to the fragment
        # the check exists to reject.
        candidate = _shorten(text)
        if _is_summary_like(candidate):
            return candidate
    return ""


def _is_summary_like(text: str) -> bool:
    """Whether a derived line is worth showing as a summary.

    A wrong summary is worse than none: it makes the index look maintained
    while misdescribing the page. So a fragment ("manufacturing partners..."),
    a list marker ("1.") or a bare label ("Frontmatter note.") is rejected and
    the row shows nothing — and `wiki_lint` raises `missing-summary`, which is
    the defect that actually needs fixing.
    """
    return (len(text) >= 40 and len(text.split()) >= 6
            and bool(re.match(r"[A-Z(\"']", text)) and not text.endswith(":"))


def _shorten(text: str, limit: int = 140) -> str:
    """First sentence, capped at `limit` on a word boundary."""
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _page_entry(ticker_dir: Path, path: Path) -> dict:
    """Everything the index needs about one page, read once."""
    post = frontmatter.load(path)
    meta = post.metadata
    questions = [q for q in (meta.get("open_questions") or []) if str(q).strip()]
    return {
        "name": _page_name(ticker_dir, path),
        "href": path.relative_to(ticker_dir / WIKI_SUBDIR).as_posix(),
        "summary": _summary(post),
        "updated": str(meta.get("updated_at", ""))[:10],
        "sources": len(meta.get("built_from") or []),
        "questions": [str(q) for q in questions],
        "dirty": bool(meta.get("dirty")),
    }


def update_index(ticker_dir: Path, sections_cfg: dict | None = None) -> Path:
    """Regenerate `wiki/00_index.md` as the wiki's navigation page (§14.2).

    Deterministic and idempotent, like the source manifest: the index is
    generated, never hand-edited, so regenerating must not show up as a change.
    That rule is why nothing here is stamped with the current time — a
    generated-at line would make every rebuild a diff.

    `sections_cfg` supplies section titles and report order. Without it the
    index still builds, ordering section pages by `SECTION_IDS` and titling
    them by page name, so callers that have no config (and the tests) are not
    forced to load one.
    """
    wiki = ticker_dir / WIKI_SUBDIR
    wiki.mkdir(parents=True, exist_ok=True)

    titles = _section_titles(sections_cfg)
    entries = {}
    for path in _pages(ticker_dir):
        entry = _page_entry(ticker_dir, path)
        entries[entry["name"]] = entry

    lines = [
        f"# {ticker_dir.name} — wiki",
        "",
        "Working notes behind each report section. These are silver (§8.1): cite",
        "the bronze ids a page carries, never the page itself.",
        "",
        "[Phase journal](log.md) · "
        "[Question ledger](../research/questions.json) · "
        "[Latest report](../reports/latest/report.md)",
        "",
        "## Report sections",
        "",
        "| # | Page | Summary | Updated | Sources | Open Qs | Status |",
        "|---:|---|---|---|---:|---:|---|",
    ]

    for number, section in enumerate(SECTION_IDS, start=1):
        title = titles.get(section, section)
        entry = entries.pop(section, None)
        if entry is None:
            # A section with no page is the most important thing this table can
            # say, and the old index — which listed only the files it found —
            # could not say it at all.
            lines.append(f"| {number} | {cell(title)} | — | — | — | — | "
                         f"not written |")
            continue
        lines.append(
            f"| {number} | [{cell(title)}]({entry['href']}) | "
            f"{cell(entry['summary'])} | {entry['updated']} | "
            f"{entry['sources']} | {len(entry['questions'])} | "
            f"{'dirty' if entry['dirty'] else ''} |"
        )

    entity = [e for name, e in entries.items() if name.startswith("entities/")]
    other = [e for name, e in entries.items() if not name.startswith("entities/")]

    for heading, group in (("Entity pages", entity), ("Other pages", other)):
        if not group:
            continue
        lines += ["", f"## {heading}", "",
                  "| Page | Summary | Updated | Sources |",
                  "|---|---|---|---:|"]
        for entry in sorted(group, key=lambda e: e["name"]):
            lines.append(
                f"| [{cell(entry['name'])}]({entry['href']}) | "
                f"{cell(entry['summary'])} | {entry['updated']} | "
                f"{entry['sources']} |")

    lines += _open_questions_rollup(ticker_dir, titles)

    out = wiki / INDEX_NAME
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _section_titles(sections_cfg: dict | None) -> dict[str, str]:
    if not sections_cfg:
        return {}
    sections = sections_cfg.get("sections") or {}
    return {sid: str(cfg.get("title") or sid)
            for sid, cfg in sections.items() if isinstance(cfg, dict)}


def _open_questions_rollup(ticker_dir: Path, titles: dict[str, str]) -> list[str]:
    """Every page's `open_questions`, grouped by page.

    The single most useful thing the index can carry: it answers "what is still
    unknown" without opening seven 60KB pages. Ordered by report order, then by
    page name, so the file stays byte-stable across rebuilds.
    """
    entries = [_page_entry(ticker_dir, path) for path in _pages(ticker_dir)]
    with_questions = [e for e in entries if e["questions"]]
    if not with_questions:
        return []

    def order(entry: dict) -> tuple[int, str]:
        name = entry["name"]
        index = SECTION_IDS.index(name) if name in SECTION_IDS else len(SECTION_IDS)
        return (index, name)

    total = sum(len(e["questions"]) for e in with_questions)
    lines = ["", "## Open questions", "",
             f"{total} open across {len(with_questions)} "
             f"page{'s' if len(with_questions) != 1 else ''}.", ""]
    for entry in sorted(with_questions, key=order):
        name = entry["name"]
        title = titles.get(name, name)
        lines.append(f"### [{title}]({entry['href']})")
        lines.append("")
        for question in entry["questions"]:
            lines.append(f"- {_shorten(' '.join(question.split()), limit=240)}")
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def append_log(ticker_dir: Path, entry: str, now: datetime | None = None, *,
               agents: int | None = None, tokens: int | None = None,
               minutes: float | None = None, run: str | None = None) -> Path:
    """Append one timestamped line to `wiki/log.md`, the append-only journal.

    §23.4 keeps this a PHASE journal rather than an audit log — one entry per
    phase boundary, written by the orchestrating skill. The optional cost
    fields do not change that; they answer the question the journal could not
    previously answer at all ("what did that phase cost?") and point at
    `run_log.md`, which is the audit log.

    With none of them supplied the output is byte-identical to what it has
    always been, so the six skills that call this keep working while they are
    updated one at a time.
    """
    log = ticker_dir / WIKI_SUBDIR / LOG_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    lines = [f"- {stamp} {entry}"]
    detail = _cost_note(agents=agents, tokens=tokens, minutes=minutes, run=run)
    if detail:
        lines.append(f"  {detail}")
    with log.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return log


def _cost_note(*, agents: int | None, tokens: int | None,
               minutes: float | None, run: str | None) -> str:
    """The indented second line of a journal entry, or `""`."""
    parts = []
    if agents is not None:
        parts.append(f"{agents} agent{'s' if agents != 1 else ''}")
    if tokens is not None:
        parts.append(f"{tokens / 1000:,.0f}k tok" if tokens >= 1000
                     else f"{tokens} tok")
    if minutes is not None:
        parts.append(f"{minutes:.1f} min")
    if not parts:
        return ""
    note = " · ".join(parts)
    if run:
        # Relative to wiki/, which is where log.md lives.
        return f"[{note}](../reports/{run}/{RUN_LOG_NAME})"
    return note


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

    declared = post.metadata.get("summary")
    if not (isinstance(declared, str) and declared.strip()):
        # The index falls back to deriving one, but a working note opens with
        # its scope and period conventions, so what gets derived describes the
        # assignment rather than the finding. One sentence from whoever wrote
        # the page beats any heuristic over it (§14.2).
        findings.append(Finding(
            "warning", "missing-summary", rel,
            "no `summary:` in frontmatter — the wiki index has to guess a "
            "one-line description from the prose (§14.2)"))

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

    # `_resolves_with_macro`, not `resolve_artifact`: `validate` gives citations
    # the `_MACRO` fallback (§8.4 check 4) and this check did not, so a page that
    # cited `fred_dgs10` for its risk-free rate and honestly recorded it in
    # `built_from` drew an advisory warning for a citation `validate` passes.
    # TOST shipped two of those.
    for ref in post.metadata.get("built_from") or []:
        ref_id = ref.get("id") if isinstance(ref, dict) else ref
        if isinstance(ref_id, str) and not _resolves_with_macro(ticker_dir, ref_id):
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


def _unindexed_pages(ticker_dir: Path) -> list[Finding]:
    """Any page absent from `00_index.md` (§22.1's set difference). The index
    is a researcher's map of the wiki; a page missing from it is a page nobody
    will read.

    Covers every page, not just entities. The narrower check could not see the
    failure that actually happens: a section page edited outside `/sra-research`
    — by a lint correction pass, say — leaves an index that still describes the
    previous version, and the section rows are the ones anybody reads.
    """
    index_path = ticker_dir / WIKI_SUBDIR / INDEX_NAME
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    findings = []
    for path in _pages(ticker_dir):
        name = _page_name(ticker_dir, path)
        if name in index_text:
            continue
        kind = "entity" if name.startswith("entities/") else "wiki"
        findings.append(Finding(
            "warning", "page-not-indexed", str(path.relative_to(ticker_dir)),
            f"{kind} page {name!r} is not listed in {INDEX_NAME} "
            f"(run: sra.py wiki-index)"))
    return findings


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
    findings += _unindexed_pages(ticker_dir)
    return findings
