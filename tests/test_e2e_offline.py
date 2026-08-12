"""Offline end-to-end gate over a recorded corpus (spec §24, §16.4, §15.3).

Drives `tests/fixtures/e2e_tree/PANW` through the deterministic half of a build
— manifest, validate, charts ordering, assemble, snapshot, validate — with stub
sections and a stub verdict standing in for the model phases.

What it defends that no unit test can: the PHASE ORDER. Each constraint below
fails silently in production if it is violated — a verdict chart with nothing
plotted on it, a report with a hole where an exhibit should be, a snapshot over
a report that never validated.

The chart RENDERING pass is deliberately not run here: `sra.py charts` drives a
headless Chrome through kaleido, which is an environment dependency rather than
a pipeline behavior, and the renderers have their own tests. What this test does
run is the ordering rule that pass participates in — `charts --verdict` refuses
to run before a verdict exists — and it places candidate PNGs the way a
successful render would.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import sra
from lib.render.runs import current_run, is_snapshotted, next_run
from lib.sections import SECTION_IDS, load_sections

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "e2e_tree"
TODAY = date(2026, 8, 11)
NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)

VERDICT = {
    "rating": "Buy", "conviction": "medium", "fair_value": 215.0,
    "horizon_months": 12, "current_price": 187.4, "implied_return_pct": 999.0,
    "valuation_method": "DCF and forward EV/S", "vs_consensus": "in line",
    "thesis": "Platformization is converting point products into contracts.",
    "key_risk": "SASE share loss to Zscaler.", "base_case_probability": 0.6,
}


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    """A private copy of the recorded corpus, with the clock pinned."""
    data_root = tmp_path / "data"
    shutil.copytree(FIXTURE / "PANW", data_root / "PANW")
    monkeypatch.setattr(sra, "_utcnow", lambda: NOW)
    return data_root


def run(*argv: str) -> int:
    return sra.main(list(argv))


def write_drafts(ticker_dir: Path, run_dir: Path) -> None:
    """What the write wave and the polish chain would have left behind."""
    titles = load_sections()["sections"]
    sections = run_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    for n, sid in enumerate(SECTION_IDS, start=1):
        body = (f"## {n}. {titles[sid]['title']}\n\n"
                f"Prose for {sid} with no citation.\n")
        if sid == "financial":
            body = (f"## {n}. {titles[sid]['title']}\n\n"
                    "Revenue grew 15% to $2.29B in Q3 FY26 "
                    "[^2026-05-21_sec_10q], while SASE pricing pressure "
                    "intensified [^2026-08-01_news_sase].\n")
        (sections / f"{sid}.md").write_text(body, encoding="utf-8")
    (run_dir / "conclusion.md").write_text(
        "## Conclusion: Investment Thesis\n\nWe rate PANW Buy on the "
        "platformization evidence [^2026-05-21_sec_10q].\n", encoding="utf-8")
    (run_dir / "verdict.json").write_text(json.dumps(VERDICT), encoding="utf-8")


def place_exhibits(ticker_dir: Path, names: list[str]) -> None:
    """Stand in for a successful `sra.py charts` pass plus `/sra-chartbook`."""
    candidates = ticker_dir / "charts" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    selected = []
    for n, name in enumerate(names, start=1):
        (candidates / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (candidates / f"{name}.json").write_text(json.dumps({
            "name": name, "title": name.replace("_", " ").title(),
            "data_sources": ["key_ratios_computed"], "derived_from_urls": [],
            "auto_caption": f"{name} as of 2026-08-10",
            "salience": {"recency_days": 1, "coverage": 1.0,
                         "variance_note": ""}}), encoding="utf-8")
        selected.append({"name": name, "section": "financial", "order": n,
                         "caption": f"{name} — Yahoo Finance, as of 2026-08-10"})
    (ticker_dir / "charts" / "chartbook.json").write_text(
        json.dumps({"selected": selected}), encoding="utf-8")


# --- the recorded corpus itself -------------------------------------------

def test_the_recorded_corpus_validates_as_shipped(tree: Path):
    """If the fixture ever stops validating, every ordering assertion below is
    measuring something that could not happen in production."""
    assert run("validate", "PANW", "--data-root", str(tree)) == 0


def test_manifest_regenerates_over_the_recorded_corpus(tree: Path):
    assert run("manifest", "PANW", "--data-root", str(tree)) == 0
    manifest = (tree / "PANW" / "sources" / "00_manifest.md").read_text(encoding="utf-8")
    assert "2026-05-21_sec_10q" in manifest
    assert "2026-08-01_news_sase" in manifest


# --- phase ordering (§16.4) -----------------------------------------------

def test_verdict_charts_are_refused_before_a_verdict_exists(tree: Path, capsys):
    """§16.4: the polish chain produces `verdict.json` BEFORE
    `charts --verdict`. Those renderers read the fair value; without one they
    would quietly draw a football field with nothing on it."""
    assert run("charts", "PANW", "--verdict", "--data-root", str(tree)) == 1
    assert "verdict" in capsys.readouterr().err


def test_assembly_is_refused_before_the_write_wave(tree: Path, capsys):
    ticker_dir = tree / "PANW"
    run_dir = next_run(ticker_dir, TODAY)
    run_dir.mkdir(parents=True)
    capsys.readouterr()

    assert run("assemble", "PANW", "--data-root", str(tree)) == 1
    assert "verdict" in capsys.readouterr().err


def test_assembly_is_refused_when_a_selected_exhibit_never_rendered(tree: Path, capsys):
    """§15.3: assembly refuses references to nonexistent chart candidates —
    charts must come before assembly, and a hole in the PDF is not a warning."""
    ticker_dir = tree / "PANW"
    run_dir = next_run(ticker_dir, TODAY)
    write_drafts(ticker_dir, run_dir)
    (ticker_dir / "charts").mkdir(exist_ok=True)
    (ticker_dir / "charts" / "chartbook.json").write_text(
        json.dumps({"selected": [{"name": "never_rendered", "section": "financial",
                                  "order": 1, "caption": "x"}]}), encoding="utf-8")
    capsys.readouterr()

    assert run("assemble", "PANW", "--data-root", str(tree)) == 1
    assert "never_rendered" in capsys.readouterr().err


def test_snapshot_is_refused_before_assembly(tree: Path, capsys):
    ticker_dir = tree / "PANW"
    run_dir = next_run(ticker_dir, TODAY)
    write_drafts(ticker_dir, run_dir)
    capsys.readouterr()

    assert run("snapshot", "PANW", "--data-root", str(tree)) == 1
    assert "report.md" in capsys.readouterr().err


# --- the full deterministic pass ------------------------------------------

def _build(tree: Path, capsys) -> Path:
    """manifest → validate → (charts) → assemble → validate → snapshot."""
    ticker_dir = tree / "PANW"
    assert run("manifest", "PANW", "--data-root", str(tree)) == 0
    assert run("validate", "PANW", "--data-root", str(tree)) == 0

    run_dir = current_run(ticker_dir, TODAY)
    write_drafts(ticker_dir, run_dir)
    place_exhibits(ticker_dir, ["revenue_growth", "margin_trends"])

    # With a verdict on disk the verdict pass is no longer refused (§16.4).
    assert run("charts", "PANW", "--verdict", "--data-root", str(tree)) != 1

    assert run("assemble", "PANW", "--data-root", str(tree)) == 0
    assert run("validate", "PANW", "--data-root", str(tree)) == 0
    assert run("snapshot", "PANW", "--data-root", str(tree)) == 0
    capsys.readouterr()
    return run_dir


def test_the_deterministic_pass_produces_a_validated_snapshot(tree: Path, capsys):
    run_dir = _build(tree, capsys)
    assert (run_dir / "report.md").exists()
    assert is_snapshotted(run_dir)
    assert (tree / "PANW" / "reports" / "latest").resolve() == run_dir.resolve()


def test_citations_resolve_and_references_render_in_the_markdown(tree: Path, capsys):
    """§23.3's provenance gate, in the part an offline test can check."""
    run_dir = _build(tree, capsys)
    report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "[^1]" in report and "[^2]" in report
    assert "2026-05-21_sec_10q" not in report          # renumbered, not leaked
    assert "## References" in report
    assert "[1] PANW Q3 FY26 10-Q — SEC EDGAR, https://www.sec.gov/" in report
    assert "[2] SASE competition intensifies" in report

    citation_map = json.loads((run_dir / "citation_map.json").read_text(encoding="utf-8"))
    assert citation_map == {"1": "2026-05-21_sec_10q", "2": "2026-08-01_news_sase"}


def test_the_verdict_arithmetic_is_the_drivers_not_the_models(tree: Path, capsys):
    """The stub verdict ships a deliberately absurd 999.0."""
    run_dir = _build(tree, capsys)
    verdict = json.loads((run_dir / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["implied_return_pct"] == pytest.approx(14.73, abs=0.01)
    assert verdict["implied_return_source"] == "driver"


def test_no_internal_filename_reaches_the_report(tree: Path, capsys):
    run_dir = _build(tree, capsys)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    body = report[:report.index("## Chartbook")]
    for internal in ("structured/", "derived/", ".state.json", "00_manifest"):
        assert internal not in body, internal


def test_every_selected_exhibit_appears_at_its_section_and_in_the_appendix(
        tree: Path, capsys):
    run_dir = _build(tree, capsys)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    for name in ("revenue_growth", "margin_trends"):
        assert report.count(f"charts/candidates/{name}.png") == 2, name


def test_state_records_the_generation(tree: Path, capsys):
    _build(tree, capsys)
    state = json.loads((tree / "PANW" / ".state.json").read_text(encoding="utf-8"))
    assert state["report"]["last_generated"] == NOW.isoformat()
    assert state["report"]["sections_dirty"] == []


# --- §24: multiple same-day snapshots -------------------------------------

def test_a_second_same_day_build_suffixes_and_leaves_the_first_diffable(
        tree: Path, capsys):
    ticker_dir = tree / "PANW"
    first = _build(tree, capsys)
    first_report = (first / "report.md").read_text(encoding="utf-8")

    second = current_run(ticker_dir, TODAY)
    assert second.name == "2026-08-11_2"

    write_drafts(ticker_dir, second)
    (second / "sections" / "valuation.md").write_text(
        "## 6. Valuation\n\nA revised valuation paragraph.\n", encoding="utf-8")
    assert run("assemble", "PANW", "--data-root", str(tree)) == 0
    assert run("snapshot", "PANW", "--data-root", str(tree)) == 0
    capsys.readouterr()

    # `latest` follows the newer run; the first snapshot is untouched.
    assert (ticker_dir / "reports" / "latest").resolve() == second.resolve()
    assert (first / "report.md").read_text(encoding="utf-8") == first_report

    # §23.3's incremental gate is checkable because both runs survive.
    changed = [sid for sid in SECTION_IDS
               if (first / "sections" / f"{sid}.md").read_text(encoding="utf-8")
               != (second / "sections" / f"{sid}.md").read_text(encoding="utf-8")]
    assert changed == ["valuation"]


# --- retrieval evaluation over the recorded ledger ------------------------

def test_eval_retrieval_scores_the_recorded_answered_question(tree: Path, capsys):
    assert run("eval-retrieval", "PANW", "--data-root", str(tree)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scored"] == 1
    assert out["mean_recall"] == pytest.approx(1.0)
