#!/usr/bin/env python3
"""News source fetcher (yfinance — the only entitled company-news provider).

FMP news is 402 and Tiingo news is 403 on the current entitlements, so Yahoo is
the single news provider. Articles land in one dated roundup source; every
article URL is preserved in `cited_urls` so a news claim can be cited back to
its true origin rather than to the roundup.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import (
    fetch_cmd, find_prior_source, source_exists, validate_existing_source)
from lib.provenance import SourceMeta, make_source_id, write_source
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

# Raised from sra5's 10: a persistent KB wants more headroom than a one-shot report.
NEWS_MAX_ARTICLES = 20
NEWS_POLICY = {"policy_days": 5}


def _yf_news(ticker: str) -> list[dict]:
    import yfinance as yf  # local import: keep the module importable offline

    return yf.Ticker(ticker).news or []


def _normalize_news_item(item: dict) -> dict | None:
    """Normalize either yfinance news format to {title,url,publisher,published,summary}.

    yfinance emits the new nested shape (`item["content"]`) and, on older
    versions/endpoints, the legacy flat shape with an epoch-seconds timestamp.
    Returns None for an item without both a title and a URL — there is nothing
    citable there.
    """
    if isinstance(item.get("content"), dict):
        c = item["content"]
        title = c.get("title")
        url = (c.get("canonicalUrl") or {}).get("url")
        publisher = (c.get("provider") or {}).get("displayName", "")
        published = c.get("pubDate", "")
        summary = c.get("summary", "")
    else:
        title = item.get("title")
        url = item.get("link")
        publisher = item.get("publisher", "")
        epoch = item.get("providerPublishTime")
        published = (datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
                     if isinstance(epoch, (int, float)) else "")
        summary = item.get("summary", "")
    if not title or not url:
        return None
    return {"title": title, "url": url, "publisher": publisher,
            "published": published, "summary": summary}


def fetch_news(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    news_provider: Callable[[str], list[dict]] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Write the recent Yahoo Finance news roundup as an immutable source doc.

    Sources are immutable: a same-day re-run is a no-op (the day's roundup is
    already the current state), and a later refetch writes a new dated file whose
    `supersedes:` points back at the prior roundup.
    """
    provider = news_provider or _yf_news
    now = now or datetime.now(timezone.utc)
    # The same-day check uses the UNSUFFIXED id deliberately: `make_source_id`
    # allocates the smallest free `_<n>` suffix (§5), so calling it first would
    # return `<date>_news_yahoo_2` on a re-run — an id that exists nowhere — and the
    # no-op below could never fire, so every re-run would refetch and duplicate.
    today_sid = f"{now.date().isoformat()}_news_yahoo"
    if source_exists(ticker_dir, today_sid):   # same-day re-run: fresh no-op
        invalid = validate_existing_source(ticker_dir, today_sid, "news")
        if invalid:  # not a usable news source — don't stamp freshness over it
            return False, [], f"existing same-day news source is invalid: {invalid}"
        record_fetch(state, "news", today_sid, now, NEWS_POLICY)
        return True, [ticker_dir / "sources" / f"{today_sid}.md"], None

    sid = make_source_id("news", now.date(), topic="yahoo", ticker_dir=ticker_dir)

    try:
        raw = provider(ticker)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"news fetch failed: {exc}"
    articles = [a for a in (_normalize_news_item(i) for i in raw[:NEWS_MAX_ARTICLES]) if a]
    if not articles:
        return False, [], f"no news articles for {ticker}"

    blocks = [
        f"## {a['title']}\n\n*{a['publisher']} — {a['published']}*\n\n"
        f"{a['summary']}\n\n<{a['url']}>"
        for a in articles
    ]
    symbol = ticker.upper()
    meta = SourceMeta(
        id=sid, ticker=symbol, kind="news", source="Yahoo Finance",
        url=f"https://finance.yahoo.com/quote/{symbol}/news",
        fetched_at=now.isoformat(), as_of=now.date().isoformat(),
        title=f"{symbol} news roundup {now.date().isoformat()}",
        fetch_tool="lib/fetchers/news.py",
        fetch_cmd=fetch_cmd(ticker, "news"),
        supersedes=find_prior_source(ticker_dir, "news_yahoo"),
        # `fetch-urls` (§8.3) harvests these to pull the true origin articles
        # into bronze, so a news claim can be cited past the aggregator.
        cited_urls=[a["url"] for a in articles])
    out = write_source(ticker_dir, meta, "\n\n".join(blocks))
    record_fetch(state, "news", sid, now, NEWS_POLICY)
    return True, [out], None
