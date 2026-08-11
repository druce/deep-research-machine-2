#!/usr/bin/env python3
"""Perplexity research batch: 7 topics -> 7 immutable sources with cited_urls.

Opt-in supplement (contracts amendment 3): the primary prefetch web research runs
on the built-in deep-research Workflow over the same `prompts/prefetch_research/`
topic prompts, so this fetcher is excluded from `sra.DEFAULT_KINDS` and only runs
via `--kinds perplexity`.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fetchers.common import fetch_cmd, find_prior_source, source_exists
from lib.provenance import SourceMeta, make_source_id, write_source
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

PERPLEXITY_MODEL = "sonar-pro"
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "prefetch_research"
SYSTEM_PROMPT = (
    "You are a financial research analyst providing detailed, factual analysis "
    "of public companies. Cite sources where possible and use specific numbers, "
    "dates, and data points."
)
# (topic_slug, prompt_file, max_tokens) - max_tokens carried over from sra5's
# historical PERPLEXITY_MAX_TOKENS config (longer topics get a bigger budget).
PERPLEXITY_TOPICS: list[tuple[str, str, int]] = [
    ("news", "news.md", 6000),
    ("business-profile", "business_profile.md", 8000),
    ("executives", "executives.md", 4000),
    ("business-model", "business_model.md", 5000),
    ("competitive", "competitive.md", 5000),
    ("risk", "risk.md", 8000),
    ("thesis", "thesis.md", 8000),
]
# freshness policy for the whole dated batch (spec §5.5)
POLICY_DAYS = 30


def _perplexity_query(prompt: str, max_tokens: int) -> tuple[str, list[str]]:
    """Ask Perplexity one research question -> (markdown, cited_urls).

    Perplexity speaks the OpenAI chat-completions protocol, so the openai client
    works against its base_url. Imported lazily: the package is only needed when
    this opt-in fetcher actually runs against the live API.
    """
    from openai import OpenAI

    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError("PERPLEXITY_API_KEY not set")
    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
    resp = client.chat.completions.create(
        model=PERPLEXITY_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content or ""
    # The citations are the true origins of the content (aggregator-provenance rule,
    # spec §5.1). Perplexity has moved them around across API versions, so try each
    # shape the sonar models have used, newest-documented first.
    citations = list(getattr(resp, "citations", None) or [])
    if not citations:
        extra = getattr(resp, "model_extra", None) or {}
        citations = list(extra.get("citations") or [])
        if not citations:
            citations = [r.get("url") for r in (extra.get("search_results") or [])
                         if isinstance(r, dict) and r.get("url")]
    return content, citations


def fetch_perplexity(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    query_provider: Callable[[str, int], tuple[str, list[str]]] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Run the 7 research topics, one immutable source doc per topic.

    Partial success is success: the batch reports ok as long as at least one topic
    file was written (or already existed for today), naming the failures in the
    error string. Only an all-topics failure fails the fetcher.
    """
    provider = query_provider or _perplexity_query
    now = now or datetime.now(timezone.utc)
    symbol = ticker.upper()
    company = state.get("company_name") or symbol

    paths: list[Path] = []
    written_ids: list[str] = []
    failed: list[str] = []
    for slug, prompt_file, max_tokens in PERPLEXITY_TOPICS:
        # The same-day check uses the UNSUFFIXED id deliberately: `make_source_id`
        # allocates the smallest free `_<n>` suffix (§5), so calling it first would
        # return `..._<slug>_2` on a re-run — an id that exists nowhere — and every
        # re-run would re-query Perplexity and write a duplicate document.
        today_sid = f"{now.date().isoformat()}_perplexity_research_{slug}"
        if source_exists(ticker_dir, today_sid):
            # sources are immutable: a same-day rerun re-reports, never re-queries
            paths.append(ticker_dir / "sources" / f"{today_sid}.md")
            written_ids.append(today_sid)
            continue
        source_id = make_source_id("perplexity_research", now.date(), topic=slug,
                                   ticker_dir=ticker_dir)
        prompt = (PROMPTS_DIR / prompt_file).read_text(encoding="utf-8").format(
            company=company, symbol=symbol)
        try:
            content, cited = provider(prompt, max_tokens)
        except Exception as exc:  # one bad topic must not sink the other six
            failed.append(f"{slug} ({exc})")
            continue
        if not content.strip():
            failed.append(f"{slug} (empty response)")
            continue
        meta = SourceMeta(
            id=source_id,
            ticker=symbol,
            kind="perplexity_research",
            source="Perplexity AI",
            url="https://www.perplexity.ai",
            fetched_at=now.isoformat(),
            as_of=now.date().isoformat(),
            title=f"{symbol} {slug} research",
            fetch_tool="lib/fetchers/perplexity.py",
            fetch_cmd=fetch_cmd(ticker, "perplexity"),
            supersedes=find_prior_source(ticker_dir, f"perplexity_research_{slug}"),
            # `fetch-urls` (§8.3) harvests these: an aggregator answer is only
            # citable once its true origins are pulled into bronze (§8.2).
            cited_urls=[u for u in cited if u],
        )
        paths.append(write_source(ticker_dir, meta, content))
        written_ids.append(source_id)

    if not paths:
        return False, [], f"all perplexity topics failed: {'; '.join(failed)}"
    # The per-topic files share one freshness clock but are recorded by their REAL
    # ids: §10.1's missing-artifact check resolves each id on disk, and a synthetic
    # batch-prefix id would resolve to nothing and read as permanently stale.
    record_fetch(state, "perplexity", sorted(written_ids), now,
                 {"policy_days": POLICY_DAYS})
    err = f"perplexity topics failed: {', '.join(failed)}" if failed else None
    return True, paths, err
