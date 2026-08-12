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

4. citation resolution — every `[^id]` in a wiki page or section draft
   resolves to ticker or `_MACRO` bronze, and every numeric citation in an
   assembled report maps through `citation_map.json` to bronze,
5. derivation resolution — every `derived_from`/`built_from` id exists,
6. secret scanning — no provider key value, key-shaped string, or recorded
   credential parameter anywhere in the tree.

`wiki-lint` is deliberately NOT here: it is advisory (§22.1), and mixing
advisory findings into a fatal gate would either block builds on style or
train people to ignore the gate.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
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
    check_request_credentials,
    read_structured,
    resolve_artifact,
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

# §8.4 check 4. Matches `[^id]` for both bronze ids and the numeric citations an
# assembled report uses.
CITATION_RE = re.compile(r"\[\^([A-Za-z0-9][\w.\-]*)\]")

# §8.4 check 6. Provider key shapes, so a ROTATED key — one the current
# environment no longer holds, and which therefore no env comparison can find —
# is still caught sitting in an artifact.
# The leading boundary is load-bearing, not decoration. Without it `sk-` matches
# inside ordinary words — `mu[sk-]colossus`, `elon-mu[sk-]ai-spacex` — and any
# corpus about Elon Musk fails the fatal gate on its own URLs. The SPCX build hit
# 28 of these, every one from the word "musk". A real key never continues a word.
OPENAI_KEY_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")
FRED_KEY_RE = re.compile(r"\b[0-9a-f]{32}\b")
HEX32_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")

# A 32-char mixed-case hex string is far more often a checksum than a
# credential, so the FMP shape only fires with key-like context nearby.
# The FRED shape (lowercase-only) fires unconditionally, per §8.4.
CREDENTIAL_CONTEXT_RE = re.compile(r"(?i)(apikey|api_key|access_token|token|secret|key)")
CREDENTIAL_CONTEXT_WINDOW = 40

# A credential carried as a query parameter in a recorded url. §8.4 check 6
# rejects these when non-empty; a credential inside a structured `request`
# block is rejected even when blank, via `check_request_credentials`, because
# §5 requires it to be absent rather than masked.
CREDENTIAL_QUERY_RE = re.compile(
    r"(?i)[?&](apikey|api_key|access_token|token)=([^&\s\"'\\]+)")

# The provider keys §25 puts in .env. Their live values are compared literally,
# which catches the current key; the patterns above cover rotated ones.
PROVIDER_KEY_ENV_NAMES = (
    "FMP_API_KEY", "FRED_API_KEY", "OPENAI_API_KEY", "PERPLEXITY_API_KEY",
)

# Below this length an env value is too short to be a real credential and would
# match half the tree as a substring, firing the gate on a clean corpus.
MIN_ENV_SECRET_LEN = 8


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

# §8.3 fixes the format of an answer's URL→id map as a BARE `{url: id | null}`
# object, with no `_meta`/`data` envelope. It is a lookup table the synthesizer
# reads, not a derived artifact with its own provenance, so reading it as one
# would report a missing `_meta` on every harvested answer.
URL_MAP_SUFFIX = ".urls.json"

# §13.3 requires `derived/peers/peers_user.json`, but it records a list the user
# typed: no url, and no antecedent artifact. No §6.2 producer shape fits that,
# and claiming one would make the artifact's provenance assert something false
# about where the list came from — so it is written bare and skipped here.
# It stays resolvable by `resolve_artifact`, so naming it in a `derived_from`
# still passes check 5.
SHAPELESS_JSON_NAMES = frozenset({"peers_user.json"})


def _is_shapeless(path: Path) -> bool:
    """True for a silver JSON file that deliberately carries no `_meta`."""
    return path.name.endswith(URL_MAP_SUFFIX) or path.name in SHAPELESS_JSON_NAMES


def _iter_json(ticker_dir: Path) -> list[tuple[Path, bool]]:
    """Every durable JSON artifact, paired with whether it is bronze
    (`structured/`) or silver (`derived/`). Deliberately shapeless silver files
    are excluded — see `_is_shapeless`."""
    out: list[tuple[Path, bool]] = [
        (p, True) for p in sorted((ticker_dir / "structured").rglob("*.json"))
    ]
    out += [(p, False) for p in sorted((ticker_dir / DERIVED_SUBDIR).rglob("*.json"))
            if not _is_shapeless(p)]
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


# --- check 4: citations resolve to bronze --------------------------------

def _is_silver(path: Path, ticker_dir: Path) -> bool:
    return path.is_relative_to(ticker_dir / DERIVED_SUBDIR)


def _classify_citation(ticker_dir: Path, data_root: Path, cid: str) -> str:
    """Where does a citation id land: `"bronze"`, `"silver"`, or `"missing"`?

    §12: ticker citation resolution checks ticker bronze first, then `_MACRO`
    bronze — shared macro evidence is cited from a ticker's own pages.
    """
    path = resolve_artifact(ticker_dir, cid)
    if path is not None:
        return "silver" if _is_silver(path, ticker_dir) else "bronze"

    macro = data_root / MACRO_TICKER
    if macro.is_dir() and macro.resolve() != ticker_dir.resolve():
        path = resolve_artifact(macro, cid)
        if path is not None:
            return "silver" if _is_silver(path, macro) else "bronze"
    return "missing"


def _citation_finding(cid: str, status: str, rel: str) -> Finding | None:
    if status == "bronze":
        return None
    if status == "silver":
        return Finding(
            "error", "citation-unresolved", rel,
            f"citation [^{cid}] resolves to a SILVER artifact: silver is never a "
            f"citation target (§8.1) — cite the bronze evidence it was built from",
        )
    return Finding(
        "error", "citation-unresolved", rel,
        f"citation [^{cid}] resolves to no bronze artifact in this ticker or "
        f"{MACRO_TICKER} (§8.4 check 4)",
    )


CRITIQUE_SUFFIX = ".critique.md"


def _cited_pages(ticker_dir: Path) -> list[Path]:
    """Pages whose citations carry bronze ids directly: wiki pages and report
    section DRAFTS (§8.2). Assembled reports are handled separately, since
    their citations are numeric.

    `<section>.critique.md` is excluded. A critique is the write wave's working
    note ABOUT a draft (§15.1) — it never reaches the report, and its author
    writes shorthand like `[^10q]` when quoting the draft's own citations back
    at it. Failing the fatal gate on a scratch artifact teaches people to
    distrust the gate. `write_snapshot` already draws this line the same way,
    excluding critiques from a run's section list.
    """
    pages = sorted((ticker_dir / "wiki").rglob("*.md"))
    pages += sorted(p for p in (ticker_dir / "reports").glob("*/sections/*.md")
                    if not p.name.endswith(CRITIQUE_SUFFIX))
    return pages


def _check_citations(ticker_dir: Path, data_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _cited_pages(ticker_dir):
        rel = _rel(path, ticker_dir)
        text = path.read_text(encoding="utf-8")
        for cid in dict.fromkeys(CITATION_RE.findall(text)):
            finding = _citation_finding(cid, _classify_citation(ticker_dir, data_root, cid), rel)
            if finding is not None:
                findings.append(finding)
    return findings


def _check_assembled_reports(ticker_dir: Path, data_root: Path) -> list[Finding]:
    """§8.2's gold check: every numeric citation in an assembled report exists
    in `citation_map.json`, and every mapped id resolves to bronze. "A citation
    that fails to resolve is a build defect"."""
    findings: list[Finding] = []
    for report in sorted((ticker_dir / "reports").glob("*/report.md")):
        rel = _rel(report, ticker_dir)
        cited = list(dict.fromkeys(CITATION_RE.findall(
            report.read_text(encoding="utf-8"))))
        if not cited:
            continue

        map_path = report.parent / "citation_map.json"
        try:
            citation_map = json.loads(map_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            findings.append(Finding(
                "error", "citation-unresolved", rel,
                f"report has {len(cited)} citation(s) but no citation_map.json "
                f"beside it (§8.2)",
            ))
            continue
        except ValueError as exc:
            findings.append(Finding(
                "error", "citation-unresolved", _rel(map_path, ticker_dir),
                f"citation_map.json is not valid JSON: {exc}",
            ))
            continue

        for cid in cited:
            if cid.isdigit():
                target = citation_map.get(cid)
                if target is None:
                    findings.append(Finding(
                        "error", "citation-unresolved", rel,
                        f"numeric citation [^{cid}] has no entry in citation_map.json",
                    ))
                    continue
            else:
                # An id that never got converted to a number still has to
                # resolve; leaving it unchecked would hide an assembly bug.
                target = cid
            finding = _citation_finding(
                target, _classify_citation(ticker_dir, data_root, target), rel)
            if finding is not None:
                findings.append(finding)
    return findings


_IMAGE_TARGET_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")


def _check_report_exhibits(ticker_dir: Path) -> list[Finding]:
    """Every image in an assembled report appears exactly once (§16.2).

    Three independent emitters place charts — the dashboard template, the
    per-section exhibit embeds, and (historically) an appendix gallery. Any of
    them can regress, and only this check sees the finished document.
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


# --- check 5: derivations resolve ----------------------------------------

def _reference_ids(value: object) -> list[str]:
    """Ids out of a `derived_from`/`built_from` list, which is a list of bare
    strings in a structured `_meta` (§6) and a list of stamped
    `{"id": ..., "fetched_at": ...}` dicts in wiki frontmatter and `.state.json`
    (§7)."""
    if not isinstance(value, (list, tuple)):
        return []
    ids: list[str] = []
    for ref in value:
        if isinstance(ref, str):
            ids.append(ref)
        elif isinstance(ref, dict) and isinstance(ref.get("id"), str):
            ids.append(ref["id"])
    return ids


def _resolves_anywhere(ticker_dir: Path, data_root: Path, ref: str) -> bool:
    """Ticker artifacts first, then `_MACRO` — the same reach citations have.

    A page may CITE `fred_dgs10` (§8.4 check 4 resolves into `_MACRO`), and
    `built_from` is by definition the set of references the page cites, so a
    derivation check with shorter reach makes the same id legal in prose and
    fatal in frontmatter. The SPCX valuation page hit exactly that: it cited the
    FRED risk-free rate for its WACC, recorded it in `built_from`, and failed
    the build for it.
    """
    if resolve_artifact(ticker_dir, ref) is not None:
        return True
    macro = data_root / MACRO_TICKER
    if macro.is_dir() and macro.resolve() != ticker_dir.resolve():
        return resolve_artifact(macro, ref) is not None
    return False


def _check_derivation_refs(ticker_dir: Path, rel: str, ids: list[str],
                           field: str, data_root: Path) -> list[Finding]:
    """Unlike citation, derivation ACROSS layers is legitimate (§8.1) — silver
    is built from silver all the time — so any durable artifact satisfies
    this. What must not happen is a reference to something that is not there."""
    return [
        Finding("error", "derivation-unresolved", rel,
                f"{field} id {ref!r} resolves to no artifact under this ticker "
                f"or {MACRO_TICKER} (§8.4 check 5)")
        for ref in dict.fromkeys(ids)
        if not _resolves_anywhere(ticker_dir, data_root, ref)
    ]


def _check_derivations(ticker_dir: Path, data_root: Path) -> list[Finding]:
    findings: list[Finding] = []

    for path, _ in _iter_json(ticker_dir):
        try:
            meta, _data = read_structured(path)
        except ValueError:
            continue  # already reported as a producer-shape error
        findings += _check_derivation_refs(
            ticker_dir, _rel(path, ticker_dir), list(meta.derived_from),
            "derived_from", data_root)

    for path in sorted((ticker_dir / "wiki").rglob("*.md")):
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        findings += _check_derivation_refs(
            ticker_dir, _rel(path, ticker_dir),
            _reference_ids(post.metadata.get("built_from")), "built_from", data_root)

    state_path = ticker_dir / ".state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
        rel = _rel(state_path, ticker_dir)
        for entry in (state.get("derived") or {}).values():
            if isinstance(entry, dict):
                findings += _check_derivation_refs(
                    ticker_dir, rel, _reference_ids(entry.get("derived_from")),
                    "derived_from", data_root)
        for entry in (state.get("wiki") or {}).values():
            if isinstance(entry, dict):
                findings += _check_derivation_refs(
                    ticker_dir, rel, _reference_ids(entry.get("built_from")),
                    "built_from", data_root)
    return findings


# --- check 6: secret scanning --------------------------------------------

def _env_secrets() -> list[tuple[str, str]]:
    """Live provider key values worth comparing literally, as
    `(env_name, value)`. Short or unset values are skipped: an empty string is
    a substring of every file and would fire the gate on a clean tree."""
    found = []
    for name in PROVIDER_KEY_ENV_NAMES:
        value = os.environ.get(name, "")
        if len(value) >= MIN_ENV_SECRET_LEN:
            found.append((name, value))
    return found


def _scan_text(text: str, rel: str, env_secrets: list[tuple[str, str]]) -> list[Finding]:
    """Scan one file's raw text for credentials.

    Findings never quote the matched value: §5 forbids a raw provider key in
    any log line, and a gate that prints what it found would copy the secret
    straight into CI output.
    """
    findings: list[Finding] = []

    for name, value in env_secrets:
        if value in text:
            findings.append(Finding(
                "error", "secret", rel,
                f"contains the literal value of ${name} (§5: no raw provider key "
                f"in any artifact)",
            ))

    if OPENAI_KEY_RE.search(text):
        findings.append(Finding(
            "error", "secret", rel,
            "contains an OpenAI-shaped key (sk-...) (§8.4 check 6)"))

    if FRED_KEY_RE.search(text):
        findings.append(Finding(
            "error", "secret", rel,
            "contains a FRED-shaped key (32 lowercase hex) (§8.4 check 6)"))
    else:
        for match in HEX32_RE.finditer(text):
            window = text[max(0, match.start() - CREDENTIAL_CONTEXT_WINDOW):match.start()]
            if CREDENTIAL_CONTEXT_RE.search(window):
                findings.append(Finding(
                    "error", "secret", rel,
                    "contains a 32-hex string in credential context "
                    "(FMP-shaped key) (§8.4 check 6)"))
                break

    for match in CREDENTIAL_QUERY_RE.finditer(text):
        findings.append(Finding(
            "error", "secret", rel,
            f"records a non-empty {match.group(1)!r} query parameter (§5: a "
            f"credential parameter is omitted, never recorded)",
        ))
    return findings


def _scanned_files(ticker_dir: Path) -> list[Path]:
    """§8.4: the scan covers every artifact, log line and answer file. That is
    every markdown and JSON file in the tree — including `.state.json`, the
    wiki log, answers, and assembled reports."""
    return sorted(p for p in ticker_dir.rglob("*")
                  if p.is_file() and not p.is_symlink()
                  and p.suffix in (".md", ".json"))


def _check_secrets(ticker_dir: Path) -> list[Finding]:
    env_secrets = _env_secrets()
    findings: list[Finding] = []
    for path in _scanned_files(ticker_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable content is reported by the shape checks
        findings += _scan_text(text, _rel(path, ticker_dir), env_secrets)
    return findings


def _check_recorded_requests(ticker_dir: Path) -> list[Finding]:
    """A credential inside a recorded `request` block is rejected even when
    blank (§5: absent, not masked) — a masked value still records that the call
    carried a key, and the next person to touch the fetcher fills it back in.
    That is stricter than the raw-text URL scan, which follows §8.4's
    "non-empty" wording."""
    findings: list[Finding] = []
    for path, _ in _iter_json(ticker_dir):
        try:
            meta, _data = read_structured(path)
        except ValueError:
            continue
        findings += [Finding("error", "secret", _rel(path, ticker_dir), problem)
                     for problem in check_request_credentials(meta.request)]

    for path in _source_docs(ticker_dir):
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        request = post.metadata.get("request")
        if isinstance(request, dict):
            findings += [Finding("error", "secret", _rel(path, ticker_dir), problem)
                         for problem in check_request_credentials(request)]
    return findings


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
    findings += _check_citations(ticker_dir, data_root)
    findings += _check_assembled_reports(ticker_dir, data_root)
    findings += _check_report_exhibits(ticker_dir)
    findings += _check_derivations(ticker_dir, data_root)
    findings += _check_secrets(ticker_dir)
    findings += _check_recorded_requests(ticker_dir)
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)
