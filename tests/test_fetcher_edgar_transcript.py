"""Tests for the EDGAR and transcript fetchers, focused on §5 supersede semantics.

The rule these pin: temporal succession is NOT supersession. A new 10-K does not
replace last year's 10-K, and a new quarter's call does not replace last
quarter's — both remain true evidence for their own period. Only a restatement
(an amendment, or a refreshed copy of the same call) supersedes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lib.fetchers.edgar import fetch_filings, is_amendment
from lib.fetchers.transcript import fetch_transcript
from lib.provenance import read_source, resolve_source
from lib.statefile import init_state
from lib.validate import validate

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _tenk(filing_date: str, *, form: str = "10-K", period_end: str | None = None) -> dict:
    return {
        "form": form,
        "filing_date": filing_date,
        "period_end": period_end or filing_date,
        "accession": "0001234567-26-000001",
        "url": f"https://www.sec.gov/Archives/{filing_date}.htm",
        "items": {"Item 1": "We sell cybersecurity platforms.",
                  "Item 1A": "Competition is intense."},
    }


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- EDGAR: supersede semantics ------------------------------------------

def test_is_amendment_recognizes_the_slash_a_suffix():
    assert is_amendment({"form": "10-K/A"})
    assert is_amendment({"form": "10-q/a"})
    assert not is_amendment({"form": "10-K"})
    assert not is_amendment({})


def test_a_new_10k_does_not_supersede_the_prior_one(tmp_ticker_dir: Path):
    """§5: last year's 10-K is still true evidence for last year. Superseding it
    would archive it and make every citation to it resolve to the archive as if
    it had been corrected."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2025-09-05")}, now=NOW)
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2026-09-04")}, now=NOW)

    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-09-04_sec_10k.md")
    assert meta.supersedes is None
    # ...and the prior filing is still CURRENT, not archived
    prior = resolve_source(tmp_ticker_dir, "2025-09-05_sec_10k")
    assert prior is not None and prior.parent.name == "sources"


def test_an_amendment_supersedes_the_filing_it_restates(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2026-09-04")}, now=NOW)
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {
                      "tenk": _tenk("2026-11-10", form="10-K/A")}, now=NOW)

    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-11-10_sec_10k.md")
    assert meta.supersedes == "2026-09-04_sec_10k"
    archived = resolve_source(tmp_ticker_dir, "2026-09-04_sec_10k")
    assert archived is not None and archived.parent.name == "archive"


def test_an_amendment_with_no_prior_on_disk_supersedes_nothing(tmp_ticker_dir: Path):
    """`supersedes:` naming an id that does not resolve would fail §8.4."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {
                      "tenk": _tenk("2026-11-10", form="10-K/A")}, now=NOW)
    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-11-10_sec_10k.md")
    assert meta.supersedes is None


# --- EDGAR: provenance and idempotence -----------------------------------

def test_filing_carries_full_provenance(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2026-09-04")}, now=NOW)
    meta, body = read_source(tmp_ticker_dir / "sources" / "2026-09-04_sec_10k.md")
    assert meta.kind == "sec_filing"
    assert meta.fetch_cmd == "uv run python sra.py prefetch PANW --kinds filings"
    assert meta.as_of == "2026-09-04"
    assert "Risk Factors" in body


def test_rerunning_is_a_no_op(tmp_ticker_dir: Path):
    """Sources are immutable; a filing already on disk is left untouched, so a
    rerun must not raise FileExistsError (§7.1: rerun is the recovery path)."""
    state = init_state(tmp_ticker_dir, "PANW")
    provider = lambda t: {"tenk": _tenk("2026-09-04")}  # noqa: E731
    ok1, _p1, _e1 = fetch_filings("PANW", tmp_ticker_dir, state,
                                  filings_provider=provider, now=NOW)
    ok2, paths2, _e2 = fetch_filings("PANW", tmp_ticker_dir, state,
                                     filings_provider=provider, now=NOW)
    assert ok1 and ok2
    assert paths2 == []  # nothing new written


def test_filings_records_every_id(tmp_ticker_dir: Path):
    """§10.1's missing-artifact check can only see a deleted filing if state
    names it."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2025-09-05")}, now=NOW)
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2026-09-04")}, now=NOW)
    assert "2026-09-04_sec_10k" in state["data"]["filings"]["current_ids"]
    assert state["data"]["filings"]["policy"] == "on_new_filing"


def test_nothing_extractable_is_a_failure(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_filings("PANW", tmp_ticker_dir, state,
                                    filings_provider=lambda t: {}, now=NOW)
    assert not ok and "no 10-K" in err


def test_filings_pass_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_filings("PANW", tmp_ticker_dir, state,
                  filings_provider=lambda t: {"tenk": _tenk("2026-09-04")}, now=NOW)
    assert _errors(tmp_ticker_dir) == []


# --- transcript: supersede semantics --------------------------------------

def _call(quarter: int, year: int, call_date: str, content: str = "Operator: hello.") -> dict:
    return {"quarter": quarter, "year": year, "call_date": call_date, "content": content}


def test_a_new_quarter_does_not_supersede(tmp_ticker_dir: Path):
    """§5: last quarter's call remains true evidence for last quarter."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(2, 2026, "2026-02-19"),
                     now=datetime(2026, 2, 20, tzinfo=timezone.utc))
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(3, 2026, "2026-05-21"),
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))

    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-05-22_transcript.md")
    assert meta.supersedes is None
    prior = resolve_source(tmp_ticker_dir, "2026-02-20_transcript")
    assert prior is not None and prior.parent.name == "sources"


def test_a_refreshed_same_quarter_copy_supersedes(tmp_ticker_dir: Path):
    """A corrected transcript of the SAME call replaces what is on disk."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(3, 2026, "2026-05-21"),
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(
                         3, 2026, "2026-05-22", "Operator: hello (corrected)."),
                     now=datetime(2026, 5, 25, tzinfo=timezone.utc))

    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-05-25_transcript.md")
    assert meta.supersedes == "2026-05-22_transcript"
    archived = resolve_source(tmp_ticker_dir, "2026-05-22_transcript")
    assert archived is not None and archived.parent.name == "archive"


def test_an_unchanged_call_is_a_warning_not_a_write(tmp_ticker_dir: Path):
    """`on_earnings` fires on the call date but FMP publishes 12-36h later, so
    the previous quarter's transcript arrives under a new fetch date. Writing it
    would mark the kind fresh for ~90 days and silently skip the real new call."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(3, 2026, "2026-05-21"),
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    before = dict(state["data"]["transcript"])

    ok, _paths, warn = fetch_transcript(
        "PANW", tmp_ticker_dir, state,
        transcript_provider=lambda t: _call(3, 2026, "2026-05-21"),
        now=datetime(2026, 5, 23, tzinfo=timezone.utc))
    assert ok and "unchanged" in warn
    assert not (tmp_ticker_dir / "sources" / "2026-05-23_transcript.md").exists()
    assert state["data"]["transcript"] == before  # stays stale, so the next run retries


def test_same_day_rerun_is_a_no_op(tmp_ticker_dir: Path):
    calls: list[str] = []

    def provider(t):
        calls.append(t)
        return _call(3, 2026, "2026-05-21")

    state = init_state(tmp_ticker_dir, "PANW")
    fetch_transcript("PANW", tmp_ticker_dir, state, transcript_provider=provider,
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    fetch_transcript("PANW", tmp_ticker_dir, state, transcript_provider=provider,
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    assert len(calls) == 1  # the provider is not called again


def test_transcript_records_no_credential_in_provenance(tmp_ticker_dir: Path):
    """§5: the endpoint is recorded, the apikey never is."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(3, 2026, "2026-05-21"),
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-05-22_transcript.md")
    assert "apikey" not in (meta.url or "")
    assert "apikey" not in str(meta.request)
    assert _errors(tmp_ticker_dir) == []


def test_transcript_as_of_is_the_call_date(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_transcript("PANW", tmp_ticker_dir, state,
                     transcript_provider=lambda t: _call(3, 2026, "2026-05-21"),
                     now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-05-22_transcript.md")
    assert meta.as_of == "2026-05-21"
