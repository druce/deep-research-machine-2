"""Tests for the refresh-supersede source fetchers (spec §5, §8.2, §8.3).

These three differ from edgar/transcript: a refreshed Wikipedia article, news
roundup or research answer REPLACES the one on disk — it is the same evidence
item restated, not a new period. `cited_urls` matters because `fetch-urls`
(§8.3) harvests it to pull true origins into bronze, so an aggregator claim can
be cited past the aggregator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lib.fetchers.news import fetch_news
from lib.fetchers.wikipedia import DEPENDS_ON as WIKI_DEPENDS_ON
from lib.fetchers.wikipedia import fetch_wikipedia
from lib.provenance import read_source, resolve_source
from lib.statefile import init_state
from lib.validate import validate

DAY1 = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
DAY2 = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

FAKE_PAGE = {"title": "Palo Alto Networks", "url": "https://en.wikipedia.org/wiki/PANW",
             "summary": "A cybersecurity company.", "content": "Founded in 2005."}

# yfinance's nested `content` shape (the current one); `_normalize_news_item`
# also accepts the legacy flat shape with an epoch `providerPublishTime`.
FAKE_NEWS = [
    {"content": {"title": "PANW beats",
                 "canonicalUrl": {"url": "https://reuters.com/panw-beats"},
                 "provider": {"displayName": "Reuters"},
                 "pubDate": "2026-07-30", "summary": "Revenue grew."}},
    {"content": {"title": "PANW guides up",
                 "canonicalUrl": {"url": "https://bloomberg.com/panw-guides"},
                 "provider": {"displayName": "Bloomberg"},
                 "pubDate": "2026-07-29", "summary": "Guidance raised."}},
]


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- wikipedia ------------------------------------------------------------

def test_wikipedia_depends_on_profile():
    """§11.1's wave edge: it searches on state["company_name"]."""
    assert WIKI_DEPENDS_ON == ("profile",)


def test_wikipedia_searches_on_the_company_name(tmp_ticker_dir: Path):
    seen: list[str] = []
    state = init_state(tmp_ticker_dir, "PANW")
    state["company_name"] = "Palo Alto Networks, Inc."

    def provider(company, symbol):
        seen.append(company)
        return FAKE_PAGE

    fetch_wikipedia("PANW", tmp_ticker_dir, state, page_provider=provider, now=DAY1)
    assert seen == ["Palo Alto Networks, Inc."]


def test_wikipedia_writes_provenance(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_wikipedia("PANW", tmp_ticker_dir, state,
                                      page_provider=lambda c, s: FAKE_PAGE, now=DAY1)
    assert ok and err is None
    meta, body = read_source(tmp_ticker_dir / "sources" / "2026-07-30_wikipedia.md")
    assert meta.kind == "wikipedia"
    assert meta.fetch_cmd == "uv run python sra.py prefetch PANW --kinds wikipedia"
    assert "Founded in 2005." in body


def test_a_refreshed_article_supersedes_the_prior_one(tmp_ticker_dir: Path):
    """Same evidence item restated — contrast a new 10-K, which is a new period."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_wikipedia("PANW", tmp_ticker_dir, state,
                    page_provider=lambda c, s: FAKE_PAGE, now=DAY1)
    fetch_wikipedia("PANW", tmp_ticker_dir, state,
                    page_provider=lambda c, s: FAKE_PAGE, now=DAY2)

    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-08-05_wikipedia.md")
    assert meta.supersedes == "2026-07-30_wikipedia"
    archived = resolve_source(tmp_ticker_dir, "2026-07-30_wikipedia")
    assert archived is not None and archived.parent.name == "archive"


def test_wikipedia_same_day_rerun_does_not_refetch(tmp_ticker_dir: Path):
    """The regression the unsuffixed-id check exists for: make_source_id would
    hand back `<date>_wikipedia_2` on a re-run, the no-op could never fire, and
    every re-run would refetch and write a duplicate document."""
    calls: list[str] = []

    def provider(company, symbol):
        calls.append(symbol)
        return FAKE_PAGE

    state = init_state(tmp_ticker_dir, "PANW")
    fetch_wikipedia("PANW", tmp_ticker_dir, state, page_provider=provider, now=DAY1)
    fetch_wikipedia("PANW", tmp_ticker_dir, state, page_provider=provider, now=DAY1)
    assert len(calls) == 1
    assert not (tmp_ticker_dir / "sources" / "2026-07-30_wikipedia_2.md").exists()


def test_wikipedia_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_wikipedia("PANW", tmp_ticker_dir, state,
                    page_provider=lambda c, s: FAKE_PAGE, now=DAY1)
    assert _errors(tmp_ticker_dir) == []


def test_wikipedia_provider_error_is_returned(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")

    def boom(company, symbol):
        raise LookupError("no page found")

    ok, _paths, err = fetch_wikipedia("PANW", tmp_ticker_dir, state,
                                      page_provider=boom, now=DAY1)
    assert not ok and "no page found" in err


# --- news -----------------------------------------------------------------

def test_news_preserves_cited_urls(tmp_ticker_dir: Path):
    """§8.3: fetch-urls harvests these so a news claim can be cited back to the
    original article rather than terminating at the aggregator."""
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_news("PANW", tmp_ticker_dir, state,
                                 news_provider=lambda t: FAKE_NEWS, now=DAY1)
    assert ok and err is None
    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-07-30_news_yahoo.md")
    assert meta.cited_urls == ["https://reuters.com/panw-beats",
                               "https://bloomberg.com/panw-guides"]


def test_news_writes_provenance_and_body(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_news("PANW", tmp_ticker_dir, state,
               news_provider=lambda t: FAKE_NEWS, now=DAY1)
    meta, body = read_source(tmp_ticker_dir / "sources" / "2026-07-30_news_yahoo.md")
    assert meta.kind == "news"
    assert meta.fetch_cmd == "uv run python sra.py prefetch PANW --kinds news"
    assert "PANW beats" in body and "Reuters" in body


def test_a_refreshed_roundup_supersedes(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_news("PANW", tmp_ticker_dir, state,
               news_provider=lambda t: FAKE_NEWS, now=DAY1)
    fetch_news("PANW", tmp_ticker_dir, state,
               news_provider=lambda t: FAKE_NEWS, now=DAY2)
    meta, _ = read_source(tmp_ticker_dir / "sources" / "2026-08-05_news_yahoo.md")
    assert meta.supersedes == "2026-07-30_news_yahoo"


def test_news_same_day_rerun_does_not_refetch(tmp_ticker_dir: Path):
    calls: list[str] = []

    def provider(t):
        calls.append(t)
        return FAKE_NEWS

    state = init_state(tmp_ticker_dir, "PANW")
    fetch_news("PANW", tmp_ticker_dir, state, news_provider=provider, now=DAY1)
    fetch_news("PANW", tmp_ticker_dir, state, news_provider=provider, now=DAY1)
    assert len(calls) == 1


def test_news_with_no_articles_fails(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_news("PANW", tmp_ticker_dir, state,
                                 news_provider=lambda t: [], now=DAY1)
    assert not ok and "no news articles" in err


def test_news_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_news("PANW", tmp_ticker_dir, state,
               news_provider=lambda t: FAKE_NEWS, now=DAY1)
    assert _errors(tmp_ticker_dir) == []


# --- perplexity (opt-in) --------------------------------------------------

def test_perplexity_is_not_a_default_kind():
    """§11.1: perplexity is an opt-in supplement; the primary prefetch web
    research runs through the deep-research Workflow (§11.2)."""
    from lib.fetchers.registry import DEFAULT_KINDS
    assert "perplexity" not in DEFAULT_KINDS
