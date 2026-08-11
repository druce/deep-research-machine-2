#!/usr/bin/env python3
"""Wikipedia source fetcher: immutable frontmattered article with supersedes chain."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import (
    fetch_cmd, find_prior_source, source_exists, validate_existing_source)
from lib.provenance import SourceMeta, make_source_id, write_source
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ("profile",)

# sra5 skills/config.py values, carried over with the search chain below.
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
# A lead section this short means the search landed on a stub or a disambiguation
# remnant rather than the company page; sra5 treats it as a wrong-page signal.
MIN_SUMMARY_CHARS = 100


# --- search chain, ported from sra5 skills/fetch_wikipedia/fetch_wikipedia.py --------
# Adapted: returns {"title", "url", "summary", "content"} (page.url is the canonical
# URL for provenance) instead of a (success, summary, content, title) tuple, and raises
# LookupError instead of returning None-tuples once every search term is exhausted.

def _wikipedia_page(company_name: str, symbol: str) -> dict[str, str]:
    """Search Wikipedia for the company and return its page as a dict.

    Raises LookupError when none of the search terms yields a usable page.
    """
    for term in (company_name, f"{company_name} company", f"{symbol} stock"):
        page = _try_wikipedia_search(term)
        if page is not None:
            return page
    raise LookupError(f"no Wikipedia page found for {company_name} ({symbol})")


def _try_wikipedia_search(term: str) -> dict[str, str] | None:
    """One search for *term*: top hit fetched, disambiguation retried, None if no page.

    Network/timeout errors are retried up to MAX_RETRIES times.
    """
    import wikipedia  # local import: keep the module importable without the package

    attempts = 0
    while attempts < MAX_RETRIES:
        try:
            results = wikipedia.search(term)
            if not results:
                return None
            return _fetch_page(results[0])
        except wikipedia.exceptions.DisambiguationError as exc:
            return _try_disambiguation(term, exc)
        except Exception:  # network/timeout/parse: retry, then give this term up
            attempts += 1
            if attempts >= MAX_RETRIES:
                return None
            time.sleep(RETRY_DELAY_SECONDS)
    return None


def _fetch_page(page_title: str) -> dict[str, str] | None:
    """Fetch a page by title; None when it is missing or too short to be the company."""
    import wikipedia  # local import: keep the module importable without the package

    attempts = 0
    while attempts < MAX_RETRIES:
        try:
            page = wikipedia.page(page_title, auto_suggest=False)
            if len(page.summary) < MIN_SUMMARY_CHARS:
                return None  # very short lead section — likely the wrong page
            return {"title": page.title, "url": page.url,
                    "summary": page.summary, "content": page.content}
        except wikipedia.exceptions.DisambiguationError as exc:
            return _try_disambiguation(page_title, exc)
        except wikipedia.exceptions.PageError:
            return None
        except Exception:  # network/timeout/parse: retry, then give this title up
            attempts += 1
            if attempts >= MAX_RETRIES:
                return None
            time.sleep(RETRY_DELAY_SECONDS)
    return None


def _try_disambiguation(original_term: str, exc: Exception) -> dict[str, str] | None:
    """Resolve a disambiguation page via its "(company)" option, or a "(company)" search."""
    import wikipedia  # local import: keep the module importable without the package

    for option in getattr(exc, "options", []):
        if "(company)" in option.lower():
            return _fetch_page(option)
    try:
        results = wikipedia.search(f"{original_term} (company)")
    except Exception:  # the disambiguation retry is best-effort
        return None
    return _fetch_page(results[0]) if results else None


def fetch_wikipedia(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    page_provider: Callable[[str, str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch the company's Wikipedia page and write it as an immutable source doc.

    Sources are immutable: a same-day re-run is a no-op (the day's file already is
    the current state), and a later refetch writes a new dated file whose
    `supersedes:` points back at the prior one.
    """
    provider = page_provider or _wikipedia_page
    now = now or datetime.now(timezone.utc)
    # The same-day check uses the UNSUFFIXED id deliberately: `make_source_id`
    # allocates the smallest free `_<n>` suffix (§5), so calling it first would
    # return `<date>_wikipedia_2` on a re-run — an id that exists nowhere — and the
    # no-op below could never fire, so every re-run would refetch and duplicate.
    today_sid = f"{now.date().isoformat()}_wikipedia"
    if source_exists(ticker_dir, today_sid):   # same-day re-run: fresh no-op
        invalid = validate_existing_source(ticker_dir, today_sid, "wikipedia")
        if invalid:  # not a usable wikipedia source — don't stamp freshness over it
            return False, [], f"existing same-day wikipedia source is invalid: {invalid}"
        record_fetch(state, "wikipedia", today_sid, now, {"policy_days": 90})
        return True, [ticker_dir / "sources" / f"{today_sid}.md"], None

    sid = make_source_id("wikipedia", now.date(), ticker_dir=ticker_dir)

    company = state.get("company_name") or ticker.upper()
    try:
        page = provider(company, ticker.upper())
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"wikipedia fetch failed: {exc}"

    prior = find_prior_source(ticker_dir, "wikipedia")
    meta = SourceMeta(
        id=sid, ticker=ticker.upper(), kind="wikipedia", source="Wikipedia",
        url=page["url"], fetched_at=now.isoformat(), as_of=now.date().isoformat(),
        title=page["title"], fetch_tool="lib/fetchers/wikipedia.py",
        fetch_cmd=fetch_cmd(ticker, "wikipedia"),
        # A refreshed article REPLACES the one on disk: it is the same evidence
        # item restated, not a new period (contrast edgar/transcript, §5).
        supersedes=prior)
    body = f"## Summary\n\n{page['summary']}\n\n## Full article\n\n{page['content']}"
    out = write_source(ticker_dir, meta, body)
    record_fetch(state, "wikipedia", sid, now, {"policy_days": 90})
    return True, [out], None
