#!/usr/bin/env python3
"""Earnings-call transcript source fetcher (FMP): immutable doc with supersedes chain."""
from __future__ import annotations

import re

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import (
    fetch_cmd, find_prior_source, source_exists, validate_existing_source)
from lib.fmp_http import fmp_get
from lib.provenance import SourceMeta, make_source_id, read_source, write_source
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

# Verified live 2026-07-31 against the real FMP_API_KEY (PANW). As with peers, the
# legacy `/api/v3|v4/earning_call_transcript` paths are retired; the live surface is
# `/stable/`, and it takes two calls because the content endpoint refuses to answer
# without an explicit year+quarter ("Query Error: Invalid or missing query parameter
# - year"), and `earning-call-transcript-latest` ignores `symbol` (it is a global
# newest-first feed across all issuers, not a per-symbol lookup).
#   1. /stable/earning-call-transcript-dates?symbol=PANW
#      -> [{"quarter": 3, "fiscalYear": 2026, "date": "2026-06-02"}, ...] newest first
#   2. /stable/earning-call-transcript?symbol=PANW&year=2026&quarter=3
#      -> [{"symbol", "period": "Q3", "year", "date", "content"}]
# Both answer 200 with an empty list for an unknown symbol/quarter rather than 404.
FMP_TRANSCRIPT_URL = "https://financialmodelingprep.com/stable/earning-call-transcript"
FMP_TRANSCRIPT_DATES_URL = (
    "https://financialmodelingprep.com/stable/earning-call-transcript-dates")
HTTP_TIMEOUT = 60  # transcripts are ~60KB of text


def _fmp_get(url: str, params: dict) -> list:
    """GET a /stable/ endpoint and return its JSON list.

    Delegates to `lib.fmp_http.fmp_get`, which appends the key, keeps every
    httpx message (transport errors and `resp.json()` included) from escaping
    with the keyed URL in it, and raises on FMP's dict-shaped error envelope
    instead of coercing it to []. The only local specialization is the timeout:
    transcripts are ~60KB of text.
    """
    return fmp_get(url, params, timeout=HTTP_TIMEOUT)


def _fmp_transcript(ticker: str) -> dict:
    """Default provider: the most recent earnings-call transcript from FMP.

    Raises RuntimeError when the key is missing or the endpoint is not entitled,
    and LookupError when the symbol simply has no transcript on file.
    """
    symbol = ticker.upper()
    dates = [r for r in _fmp_get(FMP_TRANSCRIPT_DATES_URL, {"symbol": symbol})
             if isinstance(r, dict) and r.get("date")]
    if not dates:
        raise LookupError(f"no transcript available for {symbol}")
    # The feed already arrives newest-first, but ordering is not contractual.
    latest = max(dates, key=lambda r: str(r["date"]))
    try:
        year, quarter = int(latest["fiscalYear"]), int(latest["quarter"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LookupError(f"unusable transcript date row for {symbol}: {latest}") from exc

    records = [r for r in _fmp_get(
        FMP_TRANSCRIPT_URL,
        {"symbol": symbol, "year": year, "quarter": quarter})
        if isinstance(r, dict) and r.get("content")]
    if not records:
        raise LookupError(f"no transcript content for {symbol} Q{quarter} {year}")
    record = records[0]
    return {
        "quarter": quarter,
        "year": year,
        # the content endpoint may stamp a datetime; provenance `as_of` is a date
        "call_date": str(record.get("date") or latest["date"])[:10],
        "content": str(record["content"]),
    }


def _prior_call_date(path: Path) -> str | None:
    """`as_of` (the call date, by design) of an already-stored transcript source."""
    try:
        meta, _ = read_source(path)
    except Exception:  # an unreadable prior must not block a fresh fetch
        return None
    return str(meta.as_of)[:10]


_PERIOD_RE = re.compile(r"\bQ(\d)\s+(\d{4})\b")


def _prior_period(path: Path) -> tuple[int, int] | None:
    """(quarter, year) recovered from a stored transcript's title, else None.

    §5's supersede rule needs to tell a REFRESHED copy of the call we already
    have from the NEXT quarter's call. The title is where the period is
    recorded ("PANW Q3 2026 Earnings Call Transcript"); the call date cannot
    stand in for it, since a corrected transcript of the same call can carry a
    different date.
    """
    try:
        meta, _body = read_source(path)
    except Exception:
        return None
    match = _PERIOD_RE.search(meta.title or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def fetch_transcript(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    transcript_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch the latest earnings-call transcript and write it as an immutable source.

    Sources are immutable: a same-day re-run is a no-op (the day's file already is
    the current state, and the provider is not called again). A later refetch
    writes a new dated file, and whether it supersedes depends on the PERIOD:
    a new quarter is new evidence and supersedes nothing (§5), while a refreshed
    copy of the same quarter replaces the one on disk.
    `as_of` is the call date, not the fetch date.

    A later refetch that returns the transcript we already stored (same call date --
    the provider has not published the new call yet) is a no-op too: no new file, and
    the kind stays stale so the next prefetch run retries. It returns success with a
    warning message, which `sra.py prefetch` surfaces under "warnings".
    """
    provider = transcript_provider or _fmp_transcript
    now = now or datetime.now(timezone.utc)
    # The same-day check uses the UNSUFFIXED id deliberately. `make_source_id`
    # allocates the smallest free `_<n>` suffix (§5), so calling it first would
    # hand back `<date>_transcript_2` on a re-run — which exists nowhere, so the
    # no-op below could never fire and every re-run would call the provider and
    # write a duplicate document.
    today_sid = f"{now.date().isoformat()}_transcript"
    if source_exists(ticker_dir, today_sid):   # same-day re-run: fresh no-op
        invalid = validate_existing_source(ticker_dir, today_sid, "transcript")
        if invalid:  # the day's file is not a usable transcript — don't stamp over it
            return False, [], f"existing same-day transcript source is invalid: {invalid}"
        record_fetch(state, "transcript", today_sid, now, {"policy": "on_earnings"})
        return True, [ticker_dir / "sources" / f"{today_sid}.md"], None

    sid = make_source_id("transcript", now.date(), ticker_dir=ticker_dir)

    subject = ticker.upper()
    try:
        transcript = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"transcript fetch failed: {exc}"

    quarter, year = transcript["quarter"], transcript["year"]
    prior = find_prior_source(ticker_dir, "transcript")
    prior_path = ticker_dir / "sources" / f"{prior}.md" if prior else None
    if prior_path is not None and _prior_call_date(prior_path) == transcript["call_date"]:
        # Same call we already have. `on_earnings` fires on the call date, but FMP
        # publishes 12-36h later, so this is the *previous* quarter's transcript
        # arriving under a new fetch date. Writing it would duplicate stored content
        # and -- worse -- record_fetch would mark the kind fresh until the next
        # earnings date, ~90 days from now, silently skipping the real new call.
        # No write, no supersedes, and deliberately no record_fetch: staying stale is
        # what makes the next prefetch run try again.
        return True, [prior_path], (
            f"transcript unchanged (call_date {transcript['call_date']}); "
            f"newest call not published yet, will retry")

    # §5: a NEW quarter is a new evidence item, not a replacement — last
    # quarter's call remains true evidence for last quarter. Only a refreshed
    # copy of the SAME quarter supersedes what is on disk.
    same_period = (prior_path is not None
                   and _prior_period(prior_path) == (quarter, year))
    meta = SourceMeta(
        id=sid, ticker=subject, kind="transcript",
        source="Financial Modeling Prep",
        # endpoint only: the apikey is never recorded in provenance (§5)
        url=f"{FMP_TRANSCRIPT_URL}?symbol={subject}&year={year}&quarter={quarter}",
        fetched_at=now.isoformat(), as_of=transcript["call_date"],
        title=f"{subject} Q{quarter} {year} Earnings Call Transcript",
        fetch_tool="lib/fetchers/transcript.py",
        fetch_cmd=fetch_cmd(ticker, "transcript"),
        request={"endpoint": FMP_TRANSCRIPT_URL,
                 "params": {"symbol": subject, "year": year, "quarter": quarter}},
        supersedes=prior if same_period else None)
    out = write_source(ticker_dir, meta, transcript["content"])
    record_fetch(state, "transcript", sid, now, {"policy": "on_earnings"})
    return True, [out], None
