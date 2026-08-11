"""Tests for `resolve_artifact`: the single answer to "where can an id live?".

Three call sites need it — `stale_kinds`'s missing-artifact check (§10.1),
`sra.py show` (§9), and `validate`'s derivation/citation resolution (§8.4
checks 4 and 5). Three copies of the layout would drift, and the failure would
be silent: a citation that resolves in one place and not another.
"""
from __future__ import annotations

from pathlib import Path

from lib.provenance import (
    SourceMeta, StructuredMeta, resolve_artifact, write_derived, write_source, write_structured,
)


def _source_meta(source_id: str, *, supersedes: str | None = None) -> SourceMeta:
    return SourceMeta(
        id=source_id, ticker="PANW", kind="news", source="yahoo",
        url="https://example.com/x", fetched_at="2026-07-30T12:00:00+00:00",
        as_of="2026-07-30", title="A headline", fetch_tool="httpx",
        fetch_cmd="sra.py prefetch PANW --kinds news", supersedes=supersedes,
    )


def _fetch_meta(artifact_id: str) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker="PANW", producer="fetch", title="Prices",
        source="yahoo", url="https://example.com/p", as_of="2026-07-30",
        provider_tool="yfinance", fetch_cmd="sra.py prefetch PANW --kinds prices",
        fetched_at="2026-07-30T12:00:00+00:00",
    )


def _model_meta(artifact_id: str) -> StructuredMeta:
    return StructuredMeta(
        id=artifact_id, ticker="PANW", producer="model", title="Ranked peers",
        source="sra-rater", as_of="2026-07-30",
        generated_at="2026-07-30T12:00:00+00:00", derived_from=["peers_candidates"],
    )


def test_resolves_a_current_source(tmp_ticker_dir: Path):
    path = write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "body")
    assert resolve_artifact(tmp_ticker_dir, "2026-07-30_news_yahoo") == path


def test_resolves_an_archived_source(tmp_ticker_dir: Path):
    write_source(tmp_ticker_dir, _source_meta("2026-07-30_news_yahoo"), "old")
    write_source(tmp_ticker_dir,
                 _source_meta("2026-07-31_news_yahoo",
                              supersedes="2026-07-30_news_yahoo"), "new")
    found = resolve_artifact(tmp_ticker_dir, "2026-07-30_news_yahoo")
    assert found is not None and found.parent.name == "archive"


def test_resolves_a_structured_artifact(tmp_ticker_dir: Path):
    path = write_structured(tmp_ticker_dir, _fetch_meta("prices_yahoo"), {"close": [1]})
    assert resolve_artifact(tmp_ticker_dir, "prices_yahoo") == path


def test_resolves_a_top_level_derived_artifact(tmp_ticker_dir: Path):
    path = write_derived(tmp_ticker_dir, _model_meta("working_note"), {"x": 1})
    assert resolve_artifact(tmp_ticker_dir, "working_note") == path


def test_resolves_a_namespaced_derived_artifact(tmp_ticker_dir: Path):
    path = write_derived(tmp_ticker_dir, _model_meta("peers_ranked"), {"x": 1},
                         namespace="peers")
    assert resolve_artifact(tmp_ticker_dir, "peers_ranked") == path


def test_sources_take_precedence_over_structured(tmp_ticker_dir: Path):
    """§9 fixes the order; bronze text is checked first."""
    source = write_source(tmp_ticker_dir, _source_meta("collide"), "body")
    write_structured(tmp_ticker_dir, _fetch_meta("collide"), {"x": 1})
    assert resolve_artifact(tmp_ticker_dir, "collide") == source


def test_structured_takes_precedence_over_derived(tmp_ticker_dir: Path):
    """Bronze before silver, so an id that exists in both resolves citable."""
    structured = write_structured(tmp_ticker_dir, _fetch_meta("collide"), {"x": 1})
    write_derived(tmp_ticker_dir, _model_meta("collide"), {"x": 2}, namespace="peers")
    assert resolve_artifact(tmp_ticker_dir, "collide") == structured


def test_an_unknown_id_resolves_to_none(tmp_ticker_dir: Path):
    assert resolve_artifact(tmp_ticker_dir, "no_such_id") is None


def test_resolution_is_stable_across_namespaces(tmp_ticker_dir: Path):
    """Two namespaces holding the same id must not resolve by directory
    enumeration order."""
    write_derived(tmp_ticker_dir, _model_meta("dup"), {"n": "answers"},
                  namespace="answers")
    write_derived(tmp_ticker_dir, _model_meta("dup"), {"n": "peers"}, namespace="peers")
    first = resolve_artifact(tmp_ticker_dir, "dup")
    assert first == resolve_artifact(tmp_ticker_dir, "dup")
    assert first is not None and first.parent.name == "answers"
