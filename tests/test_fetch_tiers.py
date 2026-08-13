"""Tiers 2 and 3 of the harvest failover chain, exercised without their deps.

Neither `playwright` nor `brightdata-sdk` is importable in the offline test
environment, and that is the point: a fallback tier's most important behaviour is
what it does when it cannot run. These tests pin that, plus the pure helpers
(user-agent construction, key redaction, result interpretation) that can be
checked without a network or a browser.

The one thing that needs faking is the SDK itself. `brightdata` is injected into
`sys.modules` as a stub, which is enough because `brightdata_fetch` only ever
touches `BrightDataClient.scrape_url` and the `status`/`data` attributes of what
it returns.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

from lib.fetchers import brightdata_fetch, browser_fetch
from lib.fmp_http import REDACTED


# --- tier 2: headless Firefox ----------------------------------------------

@pytest.fixture
def no_playwright(monkeypatch):
    """Simulate playwright being absent, whether or not it is installed here.

    Both optional deps ARE in `pyproject.toml`, so absence can no longer be had
    for free — but it is still the state on a stripped checkout, and it is the
    state these tests are about. Blanking the entry makes `import playwright`
    raise ImportError the way it would on such a machine.
    """
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)


@pytest.fixture
def no_brightdata_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "brightdata", None)


def test_browser_tier_without_playwright_fails_every_url_by_name(no_playwright):
    """A missing dependency is a named per-URL failure, never an exception."""
    urls = ["https://example.com/a", "https://example.com/b"]
    results = browser_fetch.fetch_html_batch(urls)

    assert set(results) == set(urls)
    for ok, data, err in results.values():
        assert ok is False
        assert data is None
        assert "playwright_unavailable" in err


def test_browser_tier_on_an_empty_list_does_not_start_a_browser():
    assert browser_fetch.fetch_html_batch([]) == {}
    assert asyncio.run(browser_fetch.fetch_html_batch_async([])) == {}


def test_browser_profile_dir_honours_the_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SRA_FIREFOX_PROFILE", str(tmp_path / "profile"))
    assert browser_fetch.profile_dir() == tmp_path / "profile"


def test_browser_profile_dir_defaults_inside_the_repo(monkeypatch):
    monkeypatch.delenv("SRA_FIREFOX_PROFILE", raising=False)
    assert browser_fetch.profile_dir().name == ".playwright-profile"


def test_the_user_agent_falls_back_when_no_browser_is_installed(monkeypatch):
    """No bundled Firefox to read a version from still yields a plausible UA."""
    monkeypatch.setattr(browser_fetch, "_version_cache", None)
    monkeypatch.setattr(browser_fetch, "_detect_firefox_version", lambda _p: None)

    ua = browser_fetch._firefox_user_agent(object())

    major = browser_fetch._FALLBACK_FIREFOX_VERSION.split(".")[0]
    assert f"rv:{major}.0" in ua
    assert ua.endswith(f"Firefox/{major}.0")
    assert "Macintosh; Intel Mac OS X 10.15" in ua


def test_the_user_agent_rv_token_matches_the_bundled_version(monkeypatch):
    """The `rv:` token must match the binary, or the UA is a detectable lie."""
    monkeypatch.setattr(browser_fetch, "_version_cache", None)
    monkeypatch.setattr(browser_fetch, "_detect_firefox_version", lambda _p: "133.2")

    ua = browser_fetch._firefox_user_agent(object())

    assert "rv:133.0" in ua
    assert "Firefox/133.0" in ua


def test_the_browser_tier_does_not_send_the_sec_identity(monkeypatch):
    """Tier 1's `SEC_FIRM SEC_USER` UA is what publishers block; see §B2."""
    monkeypatch.setenv("SEC_FIRM", "Acme Research")
    monkeypatch.setenv("SEC_USER", "analyst@acme.test")

    headers = browser_fetch._extra_headers("en-US")

    assert "User-Agent" not in headers
    assert "Acme Research" not in str(headers)
    assert headers["Sec-Fetch-Mode"] == "navigate"


def test_heavy_resource_types_are_blocked():
    assert "image" in browser_fetch.BLOCKED_RESOURCE_TYPES
    assert "media" in browser_fetch.BLOCKED_RESOURCE_TYPES
    assert "font" in browser_fetch.BLOCKED_RESOURCE_TYPES
    assert "document" not in browser_fetch.BLOCKED_RESOURCE_TYPES
    assert "script" not in browser_fetch.BLOCKED_RESOURCE_TYPES


def test_the_per_url_ceiling_exceeds_the_nav_timeout():
    """The outer ceiling must be strictly looser, or it pre-empts navigation."""
    assert browser_fetch.PER_URL_SLACK_MS > 0


def test_purging_datadome_cookies_tolerates_a_missing_profile(tmp_path: Path):
    browser_fetch._purge_datadome_cookies(tmp_path / "nonexistent")  # must not raise


def test_preparing_a_profile_creates_it_and_drops_compatibility_ini(tmp_path: Path):
    """A stale `compatibility.ini` makes Firefox refuse an upgraded profile."""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "compatibility.ini").write_text("[Compatibility]\n", encoding="utf-8")

    browser_fetch._prepare_profile(profile)

    assert profile.is_dir()
    assert not (profile / "compatibility.ini").exists()


# --- tier 3: Bright Data ----------------------------------------------------

def test_brightdata_tier_without_the_sdk_fails_every_url_by_name(
        no_brightdata_sdk, monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "secret-token")
    urls = ["https://example.com/a", "https://example.com/b"]

    results = brightdata_fetch.fetch_html_batch(urls)

    assert set(results) == set(urls)
    for ok, data, err in results.values():
        assert (ok, data) == (False, None)
        assert "brightdata_unavailable" in err
        assert "secret-token" not in err


def test_brightdata_tier_with_the_sdk_but_no_key_says_so(fake_brightdata, monkeypatch):
    """Reachable only with the SDK present — the install check comes first."""
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)

    results = brightdata_fetch.fetch_html_batch(["https://example.com"])

    _ok, _data, err = results["https://example.com"]
    assert "BRIGHTDATA_API_KEY is not set" in err
    assert fake_brightdata.calls == []


def test_brightdata_tier_on_an_empty_list_does_nothing():
    assert brightdata_fetch.fetch_html_batch([]) == {}


def test_a_blank_key_counts_as_unset(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "   ")
    assert brightdata_fetch.api_key() is None


def test_the_zone_is_optional(monkeypatch):
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
    assert brightdata_fetch.zone() is None
    monkeypatch.setenv("BRIGHTDATA_ZONE", "unlocker1")
    assert brightdata_fetch.zone() == "unlocker1"


def test_redaction_removes_the_live_token_from_provider_text():
    """An SDK message may quote the bare token; `sanitize` alone cannot see it."""
    text = "auth failed for abc123xyz on zone unlocker1"

    out = brightdata_fetch._redact(text, "abc123xyz")

    assert "abc123xyz" not in out
    assert REDACTED in out


def test_redaction_also_removes_query_string_secrets():
    out = brightdata_fetch._redact(
        "GET https://api.example.com/x?token=abc123&url=y failed", None)

    assert "abc123" not in out
    assert f"token={REDACTED}" in out


def test_redaction_truncates_a_traceback_sized_message():
    out = brightdata_fetch._redact("x" * 5000, None)
    assert len(out) == brightdata_fetch.MAX_ERROR_CHARS


class _Result:
    def __init__(self, status: str | None = "ready", data: object = None) -> None:
        self.status = status
        self.data = data


def test_a_ready_result_becomes_html_and_the_input_url():
    """Web Unlocker cannot report redirects, so `final_url` echoes the input."""
    ok, data, err = brightdata_fetch._interpret(
        "https://example.com/a", _Result(data="<html><p>body</p></html>"), None)

    assert (ok, err) == (True, None)
    assert data == {"html": "<html><p>body</p></html>",
                    "final_url": "https://example.com/a"}


def test_a_non_ready_status_is_a_failure():
    ok, data, err = brightdata_fetch._interpret(
        "https://example.com/a", _Result(status="error"), None)

    assert (ok, data) == (False, None)
    assert "brightdata_status" in err


def test_an_empty_body_is_a_failure():
    ok, data, err = brightdata_fetch._interpret(
        "https://example.com/a", _Result(data=""), None)

    assert (ok, data) == (False, None)
    assert "brightdata_empty" in err


def test_a_short_body_is_not_judged_here():
    """Thin-extract is `urls.py`'s single rule for all three tiers (§B1)."""
    ok, _data, err = brightdata_fetch._interpret(
        "https://example.com/a", _Result(data="<html>hi</html>"), None)

    assert (ok, err) == (True, None)


# --- tier 3 with a stubbed SDK ---------------------------------------------

@pytest.fixture
def fake_brightdata(monkeypatch):
    """Install a stub `brightdata` module and hand back its call recorder."""
    calls: list[str] = []
    behaviour: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        async def scrape_url(self, url: str, zone: str | None = None):
            calls.append(url)
            outcome = behaviour.get(url, _Result(data="<html><p>ok</p></html>"))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    module = types.ModuleType("brightdata")
    module.BrightDataClient = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "brightdata", module)
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "secret-token")
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
    return types.SimpleNamespace(calls=calls, behaviour=behaviour)


def test_a_batch_returns_one_entry_per_url(fake_brightdata):
    urls = ["https://example.com/a", "https://example.com/b"]

    results = brightdata_fetch.fetch_html_batch(urls)

    assert set(results) == set(urls)
    assert all(ok for ok, _d, _e in results.values())
    assert sorted(fake_brightdata.calls) == sorted(urls)


def test_a_repeated_url_is_requested_once(fake_brightdata):
    """Web Unlocker requests are billed, so a duplicate must not cost twice."""
    url = "https://example.com/a"

    results = brightdata_fetch.fetch_html_batch([url, url, url])

    assert list(results) == [url]
    assert fake_brightdata.calls == [url]


def test_input_order_is_preserved(fake_brightdata):
    urls = ["https://example.com/c", "https://example.com/a", "https://example.com/b"]
    assert list(brightdata_fetch.fetch_html_batch(urls)) == urls


def test_one_url_raising_does_not_lose_the_others(fake_brightdata):
    good, bad = "https://example.com/good", "https://example.com/bad"
    fake_brightdata.behaviour[bad] = RuntimeError("upstream exploded")

    results = brightdata_fetch.fetch_html_batch([good, bad])

    assert results[good][0] is True
    ok, _data, err = results[bad]
    assert ok is False
    assert "brightdata_error" in err
    assert "upstream exploded" in err


def test_a_provider_error_quoting_the_token_is_redacted(fake_brightdata):
    url = "https://example.com/a"
    fake_brightdata.behaviour[url] = RuntimeError("bad credentials: secret-token")

    _ok, _data, err = brightdata_fetch.fetch_html_batch([url])[url]

    assert "secret-token" not in err
    assert REDACTED in err
