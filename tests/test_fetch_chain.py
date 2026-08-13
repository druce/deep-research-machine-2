"""The harvest failover ladder: httpx -> headless Firefox -> Bright Data.

One httpx GET lost 76 of TOST's 202 cited URLs, almost all of it publisher
bot-blocking rather than dead links, and §8.3 turns every such loss into a claim
the synthesizer must drop. `fetch_batch` escalates those failures to heavier
transports.

What has to be true, and is pinned here:

- escalation happens only for failures a different transport could fix, and
  NEVER for an §8.3.1 refusal — "retry it through a browser" is precisely the
  bypass that control exists to stop;
- a thin body is a reason to try harder, never by itself a reason to discard;
- tiers 2 and 3 follow redirects internally, so the address we would RECORD is
  re-validated even though the individual hops cannot be;
- writes stay ordered and single-threaded however wide the fetch fans out.

Every tier is injected. Nothing here touches a network, a browser or a provider.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.fetchers.urls import (
    MIN_MARKDOWN_CHARS, canonical_url, fetch_batch, harvest_answer,
    is_escalatable)
from lib.provenance import SourceMeta, read_source, write_answer

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
URL_A = "https://investing.com/toast-q2"
URL_B = "https://businesswire.com/toast-results"


def page(markdown: str, url: str = URL_A, **extra) -> dict:
    data = {"markdown": markdown, "final_url": url, "content_type": "text/html",
            "truncated": False, "title": "A page"}
    data.update(extra)
    return data


def fat(url: str = URL_A) -> dict:
    """A body comfortably over the thin threshold."""
    return page("prose " * (MIN_MARKDOWN_CHARS // 3), url)


def tier1_returning(outcomes: dict[str, tuple]):
    """A tier-1 stand-in; records the URLs it was asked for."""
    calls: list[str] = []

    def _fetch(url: str, **_kwargs):
        calls.append(url)
        return outcomes.get(url, (False, None, "http_404: not found"))

    _fetch.calls = calls  # type: ignore[attr-defined]
    return _fetch


def batch_returning(outcomes: dict[str, tuple]):
    """A tier-2/tier-3 stand-in; records the batch it was handed."""
    calls: list[list[str]] = []

    def _fetch(urls: list[str]):
        calls.append(list(urls))
        return {u: outcomes.get(u, (False, None, "http_403: blocked")) for u in urls}

    _fetch.calls = calls  # type: ignore[attr-defined]
    return _fetch


# --- the escalation policy -------------------------------------------------

@pytest.mark.parametrize("error", [
    "http_403: blocked", "http_404: gone", "http_429: slow down",
    "http_503: unavailable", "transport_error: ConnectTimeout",
    "too_many_redirects: loop", "redirect_without_location: no header",
    "thin_content: 0 chars",
    # A heavier tier failing on its own terms says nothing about the URL, so the
    # tier after it still deserves a go.
    "playwright_unavailable: not installed", "playwright_error: nav failed",
    "playwright_timeout: 40s", "brightdata_unavailable: no key",
    "brightdata_error: boom", "brightdata_status: error",
    "brightdata_empty: no body", "tier_error: no result",
])
def test_these_failures_escalate(error):
    assert is_escalatable(error) is True


@pytest.mark.parametrize("error", [
    "redirect_to_private: 10.0.0.5", "redirect_to_loopback: 127.0.0.1",
    "redirect_to_cloud_metadata: 169.254.169.254",
])
def test_a_tier_that_redirected_somewhere_private_does_not_escalate(error):
    """The heavier tier already reached a forbidden address; a heavier one still
    would. This is the §8.3.1 refusal wearing a tier-2 hat."""
    assert is_escalatable(error) is False


@pytest.mark.parametrize("error", [
    # Every §8.3.1 refusal. Escalating any of these would make the control
    # decorative: the browser would dial exactly the address we just refused.
    "scheme_not_allowed: file", "no_host: none", "dns_failure: nxdomain",
    "bad_address: garbage", "loopback: 127.0.0.1", "link_local: 169.254.1.1",
    "private: 10.0.0.5", "cloud_metadata: 169.254.169.254",
    "multicast: 224.0.0.1", "reserved: 240.0.0.1", "unspecified: 0.0.0.0",
    # Size is size, whatever the transport.
    "too_large: 9MB",
    # A browser renders ANY content type into HTML, so escalating would store a
    # viewer shell as though it were the document.
    "mime_not_allowed: application/zip",
    "pdf_extraction_unavailable: no pypdf", "pdf_no_text: scanned",
    "pdf_unreadable: broken",
])
def test_these_failures_never_escalate(error):
    assert is_escalatable(error) is False


def test_an_unknown_reason_does_not_escalate():
    """An allowlist, so a new reason code stays put until someone decides."""
    assert is_escalatable("something_new: who knows") is False
    assert is_escalatable(None) is False


# --- the ladder ------------------------------------------------------------

def test_a_tier_one_success_never_reaches_the_heavier_tiers():
    t1 = tier1_returning({URL_A: (True, fat(), None)})
    t2, t3 = batch_returning({}), batch_returning({})

    results = fetch_batch([URL_A], tier1=t1, tier2=t2, tier3=t3)

    assert results[URL_A][0] is True
    assert t2.calls == [] and t3.calls == []


def test_a_403_escalates_to_the_browser():
    t1 = tier1_returning({URL_A: (False, None, "http_403: blocked")})
    t2 = batch_returning({URL_A: (True, fat(), None)})
    t3 = batch_returning({})

    ok, data, err = fetch_batch([URL_A], tier1=t1, tier2=t2, tier3=t3)[URL_A]

    assert (ok, err) == (True, None)
    assert data["markdown"].startswith("prose")
    assert t2.calls == [[URL_A]]
    assert t3.calls == []          # tier 2 won; tier 3 costs money


def test_a_browser_failure_escalates_to_bright_data():
    t1 = tier1_returning({URL_A: (False, None, "http_403: blocked")})
    t2 = batch_returning({URL_A: (False, None, "playwright_error: nav failed")})
    t3 = batch_returning({URL_A: (True, fat(), None)})

    ok, _data, err = fetch_batch([URL_A], tier1=t1, tier2=t2, tier3=t3)[URL_A]

    assert (ok, err) == (True, None)
    assert t3.calls == [[URL_A]]


def test_an_ssrf_refusal_stops_at_tier_one():
    """The whole point of §8.3.1 is that we do not dial these addresses."""
    t1 = tier1_returning({URL_A: (False, None, "private: resolves to 10.0.0.5")})
    t2, t3 = batch_returning({}), batch_returning({})

    ok, data, err = fetch_batch([URL_A], tier1=t1, tier2=t2, tier3=t3)[URL_A]

    assert (ok, data) == (False, None)
    assert "private" in err
    assert t2.calls == [] and t3.calls == []


def test_only_the_failures_are_escalated():
    """The browser batch must not re-fetch what tier 1 already got."""
    t1 = tier1_returning({URL_A: (True, fat(URL_A), None),
                          URL_B: (False, None, "http_403: blocked")})
    t2 = batch_returning({URL_B: (True, fat(URL_B), None)})

    fetch_batch([URL_A, URL_B], tier1=t1, tier2=t2, tier3=batch_returning({}))

    assert t2.calls == [[URL_B]]


def test_every_input_url_gets_a_result():
    t1 = tier1_returning({URL_A: (True, fat(URL_A), None)})

    results = fetch_batch([URL_A, URL_B], tier1=t1,
                          tier2=batch_returning({}), tier3=batch_returning({}))

    assert set(results) == {URL_A, URL_B}


def test_a_repeated_url_is_fetched_once():
    t1 = tier1_returning({URL_A: (True, fat(), None)})

    results = fetch_batch([URL_A, URL_A], tier1=t1,
                          tier2=batch_returning({}), tier3=batch_returning({}))

    assert list(results) == [URL_A]
    assert t1.calls == [URL_A]


def test_an_empty_batch_touches_nothing():
    t2 = batch_returning({})
    assert fetch_batch([], tier2=t2, tier3=batch_returning({})) == {}
    assert t2.calls == []


# --- thin bodies -----------------------------------------------------------

def test_a_thin_body_escalates_but_a_better_one_wins():
    thin = page("short")
    t1 = tier1_returning({URL_A: (False, thin, "thin_content: 5 chars")})
    t2 = batch_returning({URL_A: (True, fat(), None)})

    ok, data, _err = fetch_batch(
        [URL_A], tier1=t1, tier2=t2, tier3=batch_returning({}))[URL_A]

    assert ok is True
    assert data["markdown"].startswith("prose")
    assert not data.get("thin")


def test_a_body_thin_at_every_tier_is_not_stored():
    """Replaying TOST's 63 failed URLs settled this: of the bodies that stayed
    thin at every tier, ALL were bot walls, 404s or auth failures, and not one
    was a real short article. Storing the longest of them — the original design
    — would put a publisher's refusal into bronze under a plausible id, where a
    writer can cite it as fact about the company. A `null` is the true answer.
    """
    t1 = tier1_returning({URL_A: (False, page("tiny"), "thin_content: 4 chars")})
    t2 = batch_returning(
        {URL_A: (False, page("a somewhat longer body"), "thin_content: 22 chars")})
    t3 = batch_returning({URL_A: (False, page("mid"), "thin_content: 3 chars")})

    ok, data, err = fetch_batch([URL_A], tier1=t1, tier2=t2, tier3=t3)[URL_A]

    assert (ok, data) == (False, None)
    assert "thin_content" in err


def test_a_thin_body_is_not_stored_when_no_heavier_tier_is_available():
    unavailable = batch_returning(
        {URL_A: (False, None, "playwright_unavailable: not installed")})
    t1 = tier1_returning({URL_A: (False, page("brief"), "thin_content: 5 chars")})

    ok, data, _err = fetch_batch(
        [URL_A], tier1=t1, tier2=unavailable, tier3=unavailable)[URL_A]

    assert (ok, data) == (False, None)


# --- block pages -----------------------------------------------------------
#
# Every title below was served to the TOST harvest in place of real evidence.
# The length test alone cannot catch them: fintel's screening page carried 9,301
# characters and tipranks' 404 carried 8,628, both far over any sane threshold,
# because the wall comes wrapped in the publisher's full navigation chrome.

@pytest.mark.parametrize("title", [
    "You have been randomly selected for enhanced security screening.",
    "Error 404: Page Not Found - TipRanks.com",
    "404 - Page Not Found | The Motley Fool",
    "Access to this page has been denied",
    "Just a moment...",
    "Attention Required! | Cloudflare",
])
def test_a_verbose_block_page_is_refused_however_long_it_is(title):
    from lib.fetchers.urls import looks_like_block_page

    assert looks_like_block_page(title, "chrome " * 2000) is True


@pytest.mark.parametrize("body", [
    "Authentication failed. Please try again.",
    "Please enable JavaScript to continue.",
])
def test_a_short_wall_with_no_title_is_refused_on_its_body(body):
    from lib.fetchers.urls import looks_like_block_page

    assert looks_like_block_page(None, body) is True


def test_the_shortest_walls_carry_no_marker_and_are_caught_by_length_instead():
    """businesswire's wall is the 33-character "Powered and protected by
    Privacy" — no marker, no title, nothing to pattern-match. The two rules
    cover different halves of the problem, and neither alone is enough."""
    from lib.fetchers.urls import MIN_MARKDOWN_CHARS, _package, looks_like_block_page

    wall = "Powered and protected by\n\nPrivacy"
    assert looks_like_block_page(None, wall) is False

    ok, _data, err = _package(wall, URL_A, "text/html", None, MIN_MARKDOWN_CHARS)
    assert ok is False
    assert "thin_content" in err


@pytest.mark.parametrize("title", [
    "Toast Announces Second Quarter 2026 Financial Results | Morningstar",
    "Toast, Clover battle for small eateries | Restaurant Dive",
    "Earnings call transcript: Toast tops revenue in Q2 2026",
])
def test_a_real_article_is_not_mistaken_for_a_block_page(title):
    from lib.fetchers.urls import looks_like_block_page

    assert looks_like_block_page(title, "prose " * 2000) is False


def test_a_long_article_may_quote_a_block_phrase_in_its_body():
    """The body scan only applies when the phrase IS the page — otherwise an
    article about a paywall lawsuit would refuse itself."""
    from lib.fetchers.urls import looks_like_block_page

    body = "prose " * 500 + " the plaintiff saw access denied " + "prose " * 500
    assert looks_like_block_page("Paywall litigation update", body) is False


def test_a_blocked_page_escalates_before_it_is_refused():
    """A wall from one transport may not be a wall from the next."""
    blocked = page("chrome " * 2000)
    t1 = tier1_returning({URL_A: (False, blocked, "blocked_page: 'Just a moment...'")})
    t2 = batch_returning({URL_A: (True, fat(), None)})

    ok, data, _err = fetch_batch(
        [URL_A], tier1=t1, tier2=t2, tier3=batch_returning({}))[URL_A]

    assert ok is True
    assert data["markdown"].startswith("prose")


# --- error reporting -------------------------------------------------------

def test_exhausting_every_tier_merges_the_reasons():
    t1 = tier1_returning({URL_A: (False, None, "http_403: blocked")})
    t2 = batch_returning({URL_A: (False, None, "playwright_timeout: 40s")})
    t3 = batch_returning({URL_A: (False, None, "brightdata_status: error")})

    ok, data, err = fetch_batch([URL_A], tier1=t1, tier2=t2, tier3=t3)[URL_A]

    assert (ok, data) == (False, None)
    assert "http_403" in err
    assert "playwright_timeout" in err
    assert "brightdata_status" in err


def test_a_merged_error_is_bounded():
    long = "http_403: " + "x" * 5000
    t1 = tier1_returning({URL_A: (False, None, long)})

    _ok, _data, err = fetch_batch(
        [URL_A], tier1=t1,
        tier2=batch_returning({URL_A: (False, None, long)}),
        tier3=batch_returning({URL_A: (False, None, long)}))[URL_A]

    assert len(err) <= 400


# --- the post-redirect SSRF check ------------------------------------------

def test_a_browser_redirect_into_a_private_address_is_refused():
    """Tiers 2 and 3 follow redirects themselves, so the address we would RECORD
    is checked even though the individual hops cannot be (§8.3.1 residual risk).
    """
    from lib.fetchers.urls import _from_html

    ok, data, err = _from_html(
        URL_A, "<html><body><p>secrets</p></body></html>", "http://10.0.0.5/admin")

    assert (ok, data) == (False, None)
    assert "redirect_to_private" in err


def test_a_public_final_url_passes_the_post_check():
    from lib.fetchers.urls import _from_html

    body = "<html><body><p>" + ("prose " * 200) + "</p></body></html>"
    ok, data, err = _from_html(URL_A, body, "https://investing.com/real")

    assert (ok, err) == (True, None)
    assert data["final_url"] == "https://investing.com/real"


# --- canonical recovery (Bright Data cannot report redirects) --------------

def test_a_same_site_canonical_is_adopted():
    html = '<html><head><link rel="canonical" href="/actual-story"></head></html>'
    assert canonical_url(html, "https://investing.com/asked") == \
        "https://investing.com/actual-story"


def test_a_cross_site_canonical_is_ignored():
    """Otherwise a publisher chooses the url we record and the id we slug."""
    html = '<html><head><link rel="canonical" href="https://evil.test/x"></head></html>'
    assert canonical_url(html, "https://investing.com/asked") == \
        "https://investing.com/asked"


def test_unparseable_html_falls_back_to_the_requested_url():
    assert canonical_url("", "https://investing.com/asked") == \
        "https://investing.com/asked"


# --- harvest integration ---------------------------------------------------

def make_answer(ticker_dir: Path, urls: list[str]) -> Path:
    return write_answer(ticker_dir, SourceMeta(
        id="2026-08-12_prefetch_risk", ticker="PANW", kind="research_answer",
        source="sra-researcher", url="", fetched_at=NOW.isoformat(),
        as_of=NOW.date().isoformat(), title="Risk",
        fetch_tool="agents/sra-researcher.md", fetch_cmd="",
        cited_urls=urls), "Body.")


def read_map(ticker_dir: Path) -> dict:
    return json.loads(
        (ticker_dir / "derived" / "answers" / "2026-08-12_prefetch_risk.urls.json")
        .read_text(encoding="utf-8"))


def test_harvest_escalates_and_records_the_page(tmp_ticker_dir: Path):
    answer = make_answer(tmp_ticker_dir, [URL_A])
    t1 = tier1_returning({URL_A: (False, None, "http_403: blocked")})
    t2 = batch_returning({URL_A: (True, fat(), None)})

    result = harvest_answer(tmp_ticker_dir, answer, tier2=t2,
                            tier3=batch_returning({}), fetcher=t1, now=NOW)

    assert result["fetched"] == [URL_A]
    assert result["errors"] == {}
    assert read_map(tmp_ticker_dir)[URL_A] is not None


def test_harvest_records_an_unrecoverable_url_as_null(tmp_ticker_dir: Path):
    """§8.3: `null` is what tells the synthesizer the claim is not citable."""
    answer = make_answer(tmp_ticker_dir, [URL_A])
    t1 = tier1_returning({URL_A: (False, page("nearly nothing"),
                                  "thin_content: 14 chars")})
    unavailable = batch_returning({URL_A: (False, None, "playwright_unavailable: x")})

    result = harvest_answer(tmp_ticker_dir, answer, tier2=unavailable,
                            tier3=unavailable, fetcher=t1, now=NOW)

    assert result["fetched"] == []
    assert URL_A in result["errors"]
    assert read_map(tmp_ticker_dir)[URL_A] is None


def test_harvest_reports_truncated_captures(tmp_ticker_dir: Path):
    answer = make_answer(tmp_ticker_dir, [URL_A])
    t1 = tier1_returning({URL_A: (True, fat() | {"truncated": True}, None)})

    result = harvest_answer(tmp_ticker_dir, answer, tier2=batch_returning({}),
                            tier3=batch_returning({}), fetcher=t1, now=NOW)

    assert result["truncated"] == [URL_A]


def test_harvest_writes_in_url_order_however_wide_the_fetch_fans_out(
        tmp_ticker_dir: Path):
    """Ids come from `make_source_id`, which scans `sources/` — concurrent
    writers would race on its `_<n>` suffix and make ids depend on timing."""
    urls = [f"https://example.com/page-{i}" for i in range(8)]
    answer = make_answer(tmp_ticker_dir, urls)
    t1 = tier1_returning({u: (True, fat(u), None) for u in urls})

    result = harvest_answer(tmp_ticker_dir, answer, parallel=4, fetcher=t1,
                            tier2=batch_returning({}), tier3=batch_returning({}),
                            now=NOW)

    assert result["fetched"] == urls          # original order, not completion order
    mapping = read_map(tmp_ticker_dir)
    ids = [mapping[u] for u in urls]
    assert len(set(ids)) == len(urls)         # no id collisions
    for url, sid in zip(urls, ids):
        meta, _body = read_source(tmp_ticker_dir / "sources" / f"{sid}.md")
        assert meta.url == url                # each id holds the page it names


def test_parallel_fetching_still_returns_every_url(tmp_ticker_dir: Path):
    urls = [f"https://example.com/p{i}" for i in range(12)]
    answer = make_answer(tmp_ticker_dir, urls)
    t1 = tier1_returning({u: (True, fat(u), None) for u in urls})

    result = harvest_answer(tmp_ticker_dir, answer, parallel=6, fetcher=t1,
                            tier2=batch_returning({}), tier3=batch_returning({}),
                            now=NOW)

    assert sorted(result["fetched"]) == sorted(urls)
    assert sorted(t1.calls) == sorted(urls)
