"""Retrieval evaluation over the question ledger (spec §9.2).

The rule these tests defend: `eval-retrieval` is a REGRESSION test on the grep
path, so its arithmetic has to be boring and its gold set has to exclude what
grep structurally cannot return — structured artifacts and archived sources.
A metric that silently counts unreachable evidence as a miss would report a
retrieval regression that never happened.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.eval_retrieval import (
    STOPWORDS, compare_to_baseline, evaluate, gold_ids, query_terms,
)
from lib.provenance import SourceMeta, StructuredMeta, write_source, write_structured
from lib.questions import add_questions, load_questions, mark_answered


def _source(ticker_dir: Path, source_id: str, body: str, *,
            kind: str = "sec_filing", as_of: str = "2026-05-20") -> None:
    write_source(ticker_dir, SourceMeta(
        id=source_id, ticker="PANW", kind=kind, source="SEC EDGAR",
        url=f"https://www.sec.gov/{source_id}",
        fetched_at="2026-05-21T12:00:00+00:00", as_of=as_of,
        title=f"{source_id} title", fetch_tool="edgartools",
        fetch_cmd="sra.py prefetch PANW --kinds filings"), body)


def _structured(ticker_dir: Path, artifact_id: str) -> None:
    write_structured(ticker_dir, StructuredMeta(
        id=artifact_id, ticker="PANW", producer="fetch", title="Prices",
        source="Yahoo Finance", url="https://finance.yahoo.com/x",
        provider_tool="yfinance", fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-05-21T12:00:00+00:00", as_of="2026-05-20"), {"close": [1]})


def _answered(ticker_dir: Path, section: str, question: str,
              sources: list[str]) -> str:
    add_questions(ticker_dir, section, [question])
    qhash = next(q["hash"] for q in load_questions(ticker_dir)
                 if q["question"] == question)
    mark_answered(ticker_dir, qhash, sources=sources)
    return qhash


# --- query terms ----------------------------------------------------------

def test_stopwords_are_removed():
    terms = query_terms("What is the trend in gross margin for the platform?")
    assert "the" not in terms and "is" not in terms and "for" not in terms
    assert "gross" in terms and "margin" in terms and "platform" in terms


def test_terms_are_lowercased_and_deduped():
    assert query_terms("Margin margin MARGIN") == ["margin"]


def test_punctuation_is_stripped_but_hyphenated_terms_survive():
    terms = query_terms("What drove next-generation ARR growth, exactly?")
    assert "next-generation" in terms
    assert "growth" in terms
    assert not any(t.endswith(",") or t.endswith("?") for t in terms)


def test_very_short_tokens_are_dropped_but_short_real_terms_survive():
    """A one- or two-character term matches almost every document and would
    make every question look retrievable. "R&D" is three characters and is a
    genuine search term, so the cut is on length, not on shape."""
    assert query_terms("Is R&D up vs FY25?") == ["r&d", "fy25"]


def test_a_question_of_only_stopwords_yields_no_terms():
    assert query_terms("What is it about?") == []


def test_the_stopword_list_is_small_and_inline():
    """§9.2 calls for a small inline list — a linguistics dependency here would
    make the regression test depend on a corpus download."""
    assert 20 <= len(STOPWORDS) <= 120


# --- gold sets ------------------------------------------------------------

def test_gold_excludes_structured_ids(tmp_ticker_dir: Path):
    """§9.2: "structured-artifact evidence is excluded from the gold set
    because document grep cannot retrieve it"."""
    _source(tmp_ticker_dir, "2026-05-21_sec_10q", "gross margin discussion")
    _structured(tmp_ticker_dir, "prices_yahoo")
    _answered(tmp_ticker_dir, "financial", "What is gross margin?",
              ["2026-05-21_sec_10q", "prices_yahoo"])

    entry = load_questions(tmp_ticker_dir)[0]
    assert gold_ids(tmp_ticker_dir, entry) == ["2026-05-21_sec_10q"]


def test_gold_excludes_archived_sources(tmp_ticker_dir: Path):
    """Same reason: `grep` searches current evidence by default (§5), so a
    superseded source is not scored as a miss."""
    _source(tmp_ticker_dir, "2026-05-21_sec_10q", "gross margin discussion")
    _source(tmp_ticker_dir, "2026-02-01_sec_10q", "older margin discussion")
    _answered(tmp_ticker_dir, "financial", "What is gross margin?",
              ["2026-05-21_sec_10q", "2026-02-01_sec_10q"])

    archive = tmp_ticker_dir / "sources" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    old = tmp_ticker_dir / "sources" / "2026-02-01_sec_10q.md"
    (archive / "2026-02-01_sec_10q_2026-05-21.md").write_text(
        old.read_text(encoding="utf-8"), encoding="utf-8")
    old.unlink()

    entry = load_questions(tmp_ticker_dir)[0]
    assert gold_ids(tmp_ticker_dir, entry) == ["2026-05-21_sec_10q"]


# --- recall ---------------------------------------------------------------

def test_recall_is_the_share_of_gold_returned(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "doc_margin", "gross margin expanded on platform mix")
    _source(tmp_ticker_dir, "doc_other", "gross margin also discussed here")
    _source(tmp_ticker_dir, "doc_unrelated", "nothing relevant at all")
    _answered(tmp_ticker_dir, "financial",
              "What drove gross margin expansion?",
              ["doc_margin", "doc_other"])

    result = evaluate(tmp_ticker_dir, k=10)
    assert result["per_question"][0]["recall"] == pytest.approx(1.0)
    assert result["mean_recall"] == pytest.approx(1.0)


def test_a_gold_document_grep_cannot_reach_lowers_recall(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "doc_margin", "gross margin expanded on platform mix")
    _source(tmp_ticker_dir, "doc_silent", "unrelated prose")
    _answered(tmp_ticker_dir, "financial",
              "What drove gross margin expansion?",
              ["doc_margin", "doc_silent"])

    result = evaluate(tmp_ticker_dir, k=10)
    assert result["per_question"][0]["recall"] == pytest.approx(0.5)


def test_k_truncates_the_returned_set(tmp_ticker_dir: Path):
    for n in range(6):
        _source(tmp_ticker_dir, f"doc_{n}", "gross margin discussion",
                as_of=f"2026-05-0{n + 1}")
    _answered(tmp_ticker_dir, "financial", "What is gross margin?",
              ["doc_0", "doc_5"])

    at_two = evaluate(tmp_ticker_dir, k=2)["per_question"][0]
    at_ten = evaluate(tmp_ticker_dir, k=10)["per_question"][0]
    assert at_two["recall"] < at_ten["recall"] == pytest.approx(1.0)


def test_only_answered_questions_are_scored(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "doc_margin", "gross margin expanded")
    _answered(tmp_ticker_dir, "financial", "What is gross margin?", ["doc_margin"])
    add_questions(tmp_ticker_dir, "valuation", ["What multiple is fair?"])

    result = evaluate(tmp_ticker_dir, k=10)
    assert len(result["per_question"]) == 1
    assert result["per_question"][0]["section"] == "financial"


def test_a_question_with_an_empty_gold_set_is_skipped(tmp_ticker_dir: Path):
    """Answered from structured evidence alone: there is nothing grep could
    have returned, so scoring it 0.0 would defame the retrieval path."""
    _structured(tmp_ticker_dir, "prices_yahoo")
    _answered(tmp_ticker_dir, "valuation", "What is the current price?",
              ["prices_yahoo"])

    result = evaluate(tmp_ticker_dir, k=10)
    assert result["per_question"] == []
    assert result["mean_recall"] is None
    assert result["skipped"] == 1


def test_a_question_whose_terms_are_all_stopwords_is_skipped(tmp_ticker_dir: Path):
    _source(tmp_ticker_dir, "doc_a", "content")
    _answered(tmp_ticker_dir, "profile", "What is it about?", ["doc_a"])
    result = evaluate(tmp_ticker_dir, k=10)
    assert result["per_question"] == [] and result["skipped"] == 1


def test_evaluate_reports_the_terms_it_searched(tmp_ticker_dir: Path):
    """A recall number nobody can reproduce is not a regression test."""
    _source(tmp_ticker_dir, "doc_margin", "gross margin expanded")
    _answered(tmp_ticker_dir, "financial", "What drove gross margin?", ["doc_margin"])
    entry = evaluate(tmp_ticker_dir, k=10)["per_question"][0]
    assert entry["terms"] == ["drove", "gross", "margin"]
    assert entry["gold"] == ["doc_margin"]
    assert entry["returned"] == ["doc_margin"]


# --- baseline comparison --------------------------------------------------

def test_baseline_within_tolerance_passes(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mean_recall": 0.80}), encoding="utf-8")
    ok, message = compare_to_baseline({"mean_recall": 0.79}, baseline)
    assert ok and "0.79" in message


def test_a_drop_beyond_the_tolerance_fails(tmp_path: Path):
    """§9.2: CI fails if mean recall drops by more than 0.02."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mean_recall": 0.80}), encoding="utf-8")
    ok, message = compare_to_baseline({"mean_recall": 0.77}, baseline)
    assert not ok and "0.02" in message


def test_an_improvement_never_fails(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mean_recall": 0.70}), encoding="utf-8")
    ok, _ = compare_to_baseline({"mean_recall": 0.95}, baseline)
    assert ok


def test_a_missing_baseline_is_reported_not_passed(tmp_path: Path):
    ok, message = compare_to_baseline({"mean_recall": 0.9},
                                      tmp_path / "nope.json")
    assert not ok and "nope.json" in message


def test_an_unscoreable_run_cannot_pass_a_baseline(tmp_path: Path):
    """No answered question with a document gold set means no measurement —
    reporting that as "no regression" would hide a broken ledger."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mean_recall": 0.80}), encoding="utf-8")
    ok, message = compare_to_baseline({"mean_recall": None}, baseline)
    assert not ok and "no scoreable" in message.lower()


# --- the CLI --------------------------------------------------------------

def test_cli_prints_per_question_and_mean(tmp_path: Path, capsys):
    import sra

    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    ticker_dir = tmp_path / "PANW"
    _source(ticker_dir, "doc_margin", "gross margin expanded on platform mix")
    _answered(ticker_dir, "financial", "What drove gross margin?", ["doc_margin"])
    capsys.readouterr()

    assert sra.main(["eval-retrieval", "PANW", "--data-root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["k"] == 10
    assert out["mean_recall"] == pytest.approx(1.0)
    assert len(out["per_question"]) == 1


def test_cli_exits_one_on_a_baseline_regression(tmp_path: Path, capsys):
    import sra

    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    ticker_dir = tmp_path / "PANW"
    _source(ticker_dir, "doc_a", "unrelated prose")
    _answered(ticker_dir, "financial", "What drove gross margin?", ["doc_a"])
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mean_recall": 0.9}), encoding="utf-8")
    capsys.readouterr()

    assert sra.main(["eval-retrieval", "PANW", "--data-root", str(tmp_path),
                     "--baseline", str(baseline)]) == 1


def test_the_checked_in_baseline_is_an_unrecorded_placeholder():
    """§9.2's CI baseline is recorded from a real build (Task 13.4), not from
    the synthetic e2e corpus. Until then it must refuse to pass rather than
    quietly gate on a fabricated number."""
    baseline = Path(__file__).resolve().parent / "fixtures" / "retrieval_baseline.json"
    assert json.loads(baseline.read_text(encoding="utf-8"))["mean_recall"] is None
    ok, message = compare_to_baseline({"mean_recall": 0.95}, baseline)
    assert not ok and "no recorded mean_recall" in message
