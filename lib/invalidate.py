#!/usr/bin/env python3
"""Invalidation: what stops being current when new evidence arrives (§10.2, §10.3).

Two independent paths reach the same consumers, and §24 requires they be tested
separately because they detect completely different things:

**Dependency (§10.2) — evidence was REPLACED.** A source is replaced when a
current source names it in `supersedes`. A structured artifact is replaced when
its producer timestamp is newer than the stamp a consumer recorded when it used
it. That second rule is why §7 insists derivation references are stamped:
structured bronze ids are overwritten in place (only `sources/` is immutable),
so without the timestamp a refetched `profile_yahoo` is indistinguishable from
the one a question was answered against, and nothing would ever fire.

**Subscription (§10.3) — evidence ARRIVED, replacing nothing.** A new 10-Q or
transcript supersedes nothing at all; it is simply a later period. So each
section declares the data kinds it `subscribes_to` in `sections.yaml`, and a
question reopens when bronze of a subscribed kind is newer than its
`answered_at`.

Both paths are DRY-RUN by default (§10.3). `compute_invalidation` reads and
returns a report; only `apply_invalidation` writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from lib.provenance import read_source, read_structured, resolve_artifact
from lib.questions import load_questions, set_status
from lib.statefile import load_state, mark_section_dirty, save_state
from lib.wiki import WIKI_SUBDIR, mark_page_dirty

# The stamp keys a derivation reference may carry (§7's `record_derived`).
STAMP_KEYS = ("fetched_at", "computed_at", "generated_at")

CAUSE_DEPENDENCY = "dependency"
CAUSE_SUBSCRIPTION = "subscription"


@dataclass
class InvalidationReport:
    """What `invalidate` found. §10.2's dry-run output, as data.

    `reopened_questions` entries are `{hash, section, cause, evidence_id}`, and
    `cause` distinguishes the two paths — §10.2 lists "dependency cause" and
    "subscription cause" as separate lines of the output precisely so an
    operator can see WHY a question came back.
    """

    new_bronze: list[str] = field(default_factory=list)
    reopened_questions: list[dict] = field(default_factory=list)
    revived_deferred: list[str] = field(default_factory=list)
    dirty_wiki_pages: list[str] = field(default_factory=list)
    dirty_report_sections: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.new_bronze or self.reopened_questions
                    or self.revived_deferred or self.dirty_wiki_pages
                    or self.dirty_report_sections)

    def to_dict(self) -> dict:
        return {
            "new_bronze": self.new_bronze,
            "reopened_questions": self.reopened_questions,
            "revived_deferred": self.revived_deferred,
            "dirty_wiki_pages": self.dirty_wiki_pages,
            "dirty_report_sections": self.dirty_report_sections,
        }


def _parse(value: object) -> datetime | None:
    """An ISO timestamp off disk as an aware datetime, or None."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ref_stamp(ref: object) -> tuple[str, datetime | None] | None:
    """`(id, stamp)` from a stamped derivation reference, or None.

    A bare string reference has no stamp — it is what §7's `record_derived`
    refuses to store — so it is returned with `None` and simply never compares
    as replaced. Reporting it is `validate`'s job, not this module's.
    """
    if isinstance(ref, str):
        return (ref, None)
    if not isinstance(ref, dict) or not ref.get("id"):
        return None
    for key in STAMP_KEYS:
        if ref.get(key):
            return (str(ref["id"]), _parse(ref[key]))
    return (str(ref["id"]), None)


def _producer_stamp(ticker_dir: Path, artifact_id: str) -> datetime | None:
    """The artifact's own current producer timestamp, or None if unresolvable."""
    path = resolve_artifact(ticker_dir, artifact_id)
    if path is None:
        return None
    try:
        if path.suffix == ".md":
            meta, _ = read_source(path)
            return _parse(meta.fetched_at)
        meta, _ = read_structured(path)
        for key in STAMP_KEYS:
            if getattr(meta, key, None):
                return _parse(getattr(meta, key))
    except (OSError, ValueError, KeyError):
        return None
    return None


def _superseded_ids(ticker_dir: Path) -> tuple[set[str], list[str]]:
    """`(replaced_ids, superseding_ids)` from the current sources' supersede chain.

    Only CURRENT sources are read: `sources/archive/` holds documents that were
    already replaced, and re-reading their `supersedes` would keep re-reporting
    invalidations that were handled runs ago.
    """
    replaced: set[str] = set()
    superseding: list[str] = []
    for path in sorted((ticker_dir / "sources").glob("*.md")):
        try:
            meta, _ = read_source(path)
        except (OSError, ValueError, KeyError):
            continue
        if meta.supersedes:
            replaced.add(meta.supersedes)
            superseding.append(meta.id)
    return replaced, superseding


def _wiki_pages(ticker_dir: Path) -> list[tuple[str, list]]:
    """`(page_name, built_from)` for every wiki page carrying references."""
    out: list[tuple[str, list]] = []
    wiki_dir = ticker_dir / WIKI_SUBDIR
    if not wiki_dir.is_dir():
        return out
    for path in sorted(wiki_dir.rglob("*.md")):
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        refs = post.metadata.get("built_from") or []
        if refs:
            out.append((path.stem, list(refs)))
    return out


def _page_to_section(sections_cfg: dict) -> dict[str, str]:
    """Reverse of `sections.yaml`'s `wiki_page` (§10.2's last hop)."""
    return {cfg["wiki_page"]: sid
            for sid, cfg in sections_cfg.get("sections", {}).items()
            if cfg.get("wiki_page")}


def _kind_of(state: dict) -> dict[str, str]:
    """Bronze artifact id -> the data kind that produced it, from `state.data`.

    State is the authoritative answer: the id alone does not name its kind
    (`profile_yahoo` is `profile`, `2026-05-21_sec_10q` is `filings`), and
    guessing from the filename would silently mis-route the subscription rule.
    """
    out: dict[str, str] = {}
    for kind, entry in (state.get("data") or {}).items():
        if isinstance(entry, dict):
            for artifact_id in entry.get("current_ids") or []:
                out[str(artifact_id)] = kind
    return out


def compute_invalidation(
    ticker_dir: Path,
    sections_cfg: dict,
) -> InvalidationReport:
    """Find every consumer of replaced or newly-arrived evidence. Reads only.

    Dependency (§10.2) and subscription (§10.3) are computed together because
    they converge on the same consumers, but each reopened question records
    which path found it in its `cause`.
    """
    report = InvalidationReport()
    state = _safe_state(ticker_dir)
    replaced_sources, superseding = _superseded_ids(ticker_dir)
    new_bronze: list[str] = list(superseding)

    def is_stale(ref: object) -> str | None:
        """The replaced artifact id behind `ref`, or None if it is still current."""
        parsed = _ref_stamp(ref)
        if parsed is None:
            return None
        artifact_id, stamp = parsed
        if artifact_id in replaced_sources:
            return artifact_id
        if stamp is None:
            return None
        current = _producer_stamp(ticker_dir, artifact_id)
        # Strictly newer: a re-run that rewrote nothing leaves the stamp equal,
        # and must not invalidate.
        if current is not None and current > stamp:
            if artifact_id not in new_bronze:
                new_bronze.append(artifact_id)
            return artifact_id
        return None

    # --- questions ---------------------------------------------------------
    kinds = _kind_of(state)
    sections = sections_cfg.get("sections", {})
    for question in load_questions(ticker_dir):
        status = question.get("status")
        section = question.get("section")

        if status == "answered":
            evidence = next(
                (found for ref in question.get("answer_source_ids") or []
                 if (found := is_stale(ref))), None)
            if evidence is not None:
                report.reopened_questions.append({
                    "hash": question["hash"], "section": section,
                    "cause": CAUSE_DEPENDENCY, "evidence_id": evidence})
                continue
            arrived = _subscribed_arrival(
                ticker_dir, question, sections.get(section, {}), kinds)
            if arrived is not None:
                report.reopened_questions.append({
                    "hash": question["hash"], "section": section,
                    "cause": CAUSE_SUBSCRIPTION, "evidence_id": arrived})
                if arrived not in new_bronze:
                    new_bronze.append(arrived)

        elif status == "deferred":
            # §14.0: new evidence is exactly the reason to retry a question the
            # attempt floor set aside. A deferred question has no `answered_at`,
            # so arrival is measured against the whole subscribed set.
            arrived = _subscribed_arrival(
                ticker_dir, question, sections.get(section, {}), kinds)
            if arrived is not None:
                report.revived_deferred.append(question["hash"])
                if arrived not in new_bronze:
                    new_bronze.append(arrived)

    # --- wiki pages and the sections they back ----------------------------
    page_to_section = _page_to_section(sections_cfg)
    for page, refs in _wiki_pages(ticker_dir):
        if any(is_stale(ref) for ref in refs):
            report.dirty_wiki_pages.append(page)
            section = page_to_section.get(page)
            if section and section not in report.dirty_report_sections:
                report.dirty_report_sections.append(section)

    # --- state's own derivation stamps -------------------------------------
    # §10.2 names `derived.*.derived_from` as a consumer. Nothing downstream
    # reopens from it, but a refetched input still counts as new bronze, and the
    # dry-run is supposed to show what arrived.
    for entry in (state.get("derived") or {}).values():
        if isinstance(entry, dict):
            for ref in entry.get("derived_from") or []:
                is_stale(ref)

    report.new_bronze = new_bronze
    return report


def _subscribed_arrival(
    ticker_dir: Path,
    question: dict,
    section_cfg: dict,
    kinds: dict[str, str],
) -> str | None:
    """The id of newly arrived bronze of a kind this question's section
    subscribes to, newer than `answered_at`, or None (§10.3).

    A `deferred` question has no `answered_at`; any subscribed artifact revives
    it, since it was set aside for lack of evidence in the first place.
    """
    subscribed = set(section_cfg.get("subscribes_to") or [])
    if not subscribed:
        return None
    answered_at = _parse(question.get("answered_at"))

    for artifact_id, kind in kinds.items():
        if kind not in subscribed:
            continue
        stamp = _producer_stamp(ticker_dir, artifact_id)
        if stamp is None:
            continue
        if answered_at is None or stamp > answered_at:
            return artifact_id
    return None


def _safe_state(ticker_dir: Path) -> dict:
    try:
        return load_state(ticker_dir)
    except (FileNotFoundError, ValueError):
        return {}


def apply_invalidation(ticker_dir: Path, report: InvalidationReport) -> None:
    """Execute the report's transitions (§10.2, §10.3, §14.1). Idempotent.

    Idempotence comes for free from the shape of each write: a status set to a
    value it already holds, a `dirty` flag set to True, and `mark_section_dirty`
    deduping its list. A second `--apply` over the same report therefore leaves
    the tree exactly as the first did.
    """
    if report.is_empty():
        return

    for row in report.reopened_questions:
        set_status(ticker_dir, row["hash"], "reopened")
    for qhash in report.revived_deferred:
        set_status(ticker_dir, qhash, "open")
    for page in report.dirty_wiki_pages:
        mark_page_dirty(ticker_dir, page)

    if report.dirty_report_sections:
        state = _safe_state(ticker_dir)
        if state:
            for section in report.dirty_report_sections:
                mark_section_dirty(state, section)
            save_state(ticker_dir, state)
