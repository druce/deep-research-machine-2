#!/usr/bin/env python3
"""Tier 2 of the harvest failover chain: a stealth-configured headless Firefox.

Tier 1 (`lib/fetchers/urls.py`'s httpx GET) loses roughly a third of the URLs a
research phase cites, and almost all of that loss is publisher bot-blocking
rather than a dead link: the TOST prefetch harvest recorded 76 failures out of
202 URLs, concentrated in investing.com, businesswire.com, seekingalpha.com and
similar. Those hosts answer a bare HTTP client with 403 and answer a real browser
with the article. This module is that real browser.

It is deliberately TRANSPORT ONLY: it returns the page's HTML and the URL the
browser actually ended on, and `urls.py` turns that into markdown with the same
`html_to_markdown` every other tier uses. Keeping extraction in one place is what
stops the three tiers from disagreeing about what a page says, and it is also
what keeps the import graph acyclic — `urls.py` imports this module, never the
reverse.

Batch, not per-URL, and that is load-bearing. A browser launch costs seconds and
hundreds of megabytes; escalating one URL at a time from inside a thread pool
would mean one Firefox per worker. Instead the caller collects everything tier 1
failed on and hands the whole list over at once, so N URLs share ONE persistent
context and run concurrently as N pages under a semaphore.

Ported from `~/projects/newsagent/lib/fetch/browser.py` and
`playwright_runner.py`, whose anti-detection setup is the part worth copying
exactly — each measure below is there because removing it got that scraper
flagged.

Playwright is an optional dependency. If it is not installed, or its browsers
were never downloaded, every URL comes back as a named failure rather than an
exception, so the chain simply falls through to tier 3 and the offline test suite
never notices this module exists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# One nav timeout, plus slack, so `page.content()` and any other unbounded await
# is bounded too: without the outer ceiling a single wedged page hangs the whole
# `asyncio.gather` and therefore the whole harvest.
NAV_TIMEOUT_MS = 30_000
PER_URL_SLACK_MS = 10_000
DEFAULT_PARALLEL = 4
CONTEXT_CLOSE_TIMEOUT_S = 30.0

# Pinned rather than randomized, and the reason is counterintuitive enough to be
# worth stating: anti-bot vendors weight a timezone/IP-geo mismatch heavily, so a
# randomly-chosen Asia/Tokyo clock arriving from a US egress IP is a STRONGER
# automation signal than the honest one. Override both if egress moves.
PINNED_TIMEZONE = os.environ.get("PLAYWRIGHT_TIMEZONE", "America/New_York")
PINNED_LOCALE = os.environ.get("PLAYWRIGHT_LOCALE", "en-US")

# Resource types with no prose in them. Blocking these is a large speed win on
# ad-heavy publisher pages and costs nothing, because the only thing downstream
# wants from this page is its text.
BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

_FALLBACK_FIREFOX_VERSION = "141.0"
_version_cache: str | None = None


def profile_dir() -> Path:
    """The persistent Firefox profile directory.

    A persistent profile rather than a fresh context per run: cookies and any
    consent/paywall state a previous harvest earned survive, and a browser whose
    profile is empty every single time is itself a weak automation signal.

    Precedence is `$SRA_FIREFOX_PROFILE`, then `<repo>/.playwright-profile`.
    """
    override = os.environ.get("SRA_FIREFOX_PROFILE")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent.parent / ".playwright-profile"


def _purge_datadome_cookies(profile: Path) -> None:
    """Drop DataDome's block cookie before launch.

    DataDome-protected publishers issue a long-lived cookie when they flag a
    session, and a persistent profile would then carry that block forward into
    every future harvest — the profile turns from an asset into a permanent
    403. Firefox locks `cookies.sqlite` while running, so this only works
    pre-launch.
    """
    cookies = profile / "cookies.sqlite"
    if not cookies.exists():
        return
    try:
        conn = sqlite3.connect(str(cookies), timeout=5)
        try:
            deleted = conn.execute(
                "DELETE FROM moz_cookies WHERE name = 'datadome'").rowcount
            conn.commit()
            if deleted:
                logger.info("purged %d datadome cookie(s) from the profile", deleted)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("could not purge datadome cookies: %s", exc)


def _prepare_profile(profile: Path) -> None:
    """Create the profile dir and make it safe to reuse.

    `compatibility.ini` is removed because Playwright upgrades its bundled
    Firefox from time to time, and an older profile then refuses to open with a
    "newer profile" error that looks like a scraping failure but is not.
    """
    profile.mkdir(parents=True, exist_ok=True)
    compat = profile / "compatibility.ini"
    if compat.exists():
        try:
            compat.unlink()
        except OSError:
            pass
    _purge_datadome_cookies(profile)


def _viewport_and_dpr() -> tuple[dict[str, int], float]:
    """A plausible desktop viewport. Randomized to avoid a constant fingerprint."""
    viewport = random.choice([
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1440, "height": 900},
        {"width": 1536, "height": 864},
        {"width": 1280, "height": 720},
    ])
    return viewport, float(random.choice([1, 1.25, 1.5, 1.75, 2]))


def _extra_headers(locale: str) -> dict[str, str]:
    """The header set a real Firefox navigation sends.

    Note this is the one place sra6 does NOT send the `SEC_FIRM SEC_USER`
    identity that `urls.py:request_headers` uses. That identity is what sec.gov
    requires and what commercial publishers' bot rules reject; tier 1 already
    handles every host that wants it, so a URL only reaches this tier after the
    polite UA has been tried and refused.
    """
    lang = locale.split("-")[0]
    return {
        "Accept-Language": f"{lang},{locale};q=0.9",
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/webp,*/*;q=0.8"),
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "DNT": random.choice(["0", "1"]),
    }


def _detect_firefox_version(playwright: Any) -> str | None:
    """The version of the Firefox Playwright actually bundles, from its ini."""
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    try:
        exe = Path(playwright.firefox.executable_path)
    except Exception:  # noqa: BLE001 — no browser installed is not a crash here
        return None
    candidates = [
        exe.parent.parent / "Resources" / "application.ini",  # macOS .app
        exe.parent / "application.ini",                       # Linux/Windows
    ]
    ini = next((c for c in candidates if c.exists()), None)
    if ini is None:
        return None
    try:
        for line in ini.read_text(encoding="utf-8").splitlines():
            if line.startswith("Version="):
                _version_cache = line.split("=", 1)[1].strip()
                return _version_cache
    except OSError:
        pass
    return None


def _firefox_user_agent(playwright: Any) -> str:
    """A Firefox-on-macOS UA whose `rv:` matches the binary we are driving.

    Claiming a version the JS engine does not report is a trivially detectable
    inconsistency, so the major version is read out of the bundled browser
    rather than hardcoded. Firefox itself freezes the Mac platform token to
    "Intel Mac OS X 10.15" for fingerprint resistance regardless of the real
    CPU or OS, so that part IS hardcoded — matching Firefox is the goal.
    """
    version = _detect_firefox_version(playwright) or _FALLBACK_FIREFOX_VERSION
    rv = f"{version.split('.')[0]}.0"
    return (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{rv}) "
            f"Gecko/20100101 Firefox/{rv}")


def _launch_kwargs(playwright: Any) -> dict[str, Any]:
    """Context options for a headless launch that does not announce itself."""
    viewport, dpr = _viewport_and_dpr()
    return dict(
        headless=True,
        viewport=viewport,
        device_scale_factor=dpr,
        timezone_id=PINNED_TIMEZONE,
        locale=PINNED_LOCALE,
        color_scheme=random.choice(["light", "dark", "no-preference"]),
        extra_http_headers=_extra_headers(PINNED_LOCALE),
        # Playwright adds this flag by default and it is exactly what a
        # detection script looks for first.
        ignore_default_args=["--enable-automation"],
        user_agent=_firefox_user_agent(playwright),
        accept_downloads=False,
    )


async def _launch_context(playwright: Any) -> Any:
    """Open the persistent stealth context.

    `playwright_stealth` patches the obvious tells — `navigator.webdriver`,
    an empty plugin array, the WebGL vendor strings — on a throwaway page; the
    patches apply to the context, so every later page inherits them.
    """
    from playwright_stealth import Stealth

    profile = profile_dir()
    _prepare_profile(profile)
    ctx = await playwright.firefox.launch_persistent_context(
        user_data_dir=str(profile), **_launch_kwargs(playwright))
    page = await ctx.new_page()
    try:
        await Stealth().apply_stealth_async(page)
    finally:
        await page.close()
    return ctx


async def _block_heavy_resources(page: Any) -> None:
    """Abort image/media/font requests; the harvest only wants prose."""
    async def _route(route: Any) -> None:
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _route)


async def _fetch_one(ctx: Any, url: str, timeout_ms: int,
                     block_resources: bool) -> tuple[str, str]:
    """Navigate to `url` and return `(html, final_url)`. Raises on any failure."""
    page = await ctx.new_page()
    try:
        if block_resources:
            await _block_heavy_resources(page)
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        return await page.content(), page.url
    finally:
        try:
            await page.close()
        except Exception:  # noqa: BLE001 — a page that will not close is not our problem
            pass


async def fetch_html_batch_async(
    urls: list[str],
    *,
    parallel: int = DEFAULT_PARALLEL,
    timeout_ms: int = NAV_TIMEOUT_MS,
    block_resources: bool = True,
) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Fetch every URL in `urls` through one shared browser context.

    Returns `{url: (success, {"html", "final_url"}, error)}` — the repo's
    standard result triple, one per input URL, with every input URL present as a
    key whatever happened to it.

    Nothing raises. A missing dependency, a missing browser binary, a navigation
    error and a per-URL timeout are all recorded as failures, because this is a
    fallback tier: its job when it cannot run is to get out of the way.
    """
    if not urls:
        return {}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {u: (False, None, "playwright_unavailable: playwright is not installed")
                for u in urls}

    results: dict[str, tuple[bool, dict | None, str | None]] = {}
    sem = asyncio.Semaphore(max(1, parallel))
    ceiling_s = (timeout_ms + PER_URL_SLACK_MS) / 1000.0

    try:
        async with async_playwright() as playwright:
            try:
                ctx = await _launch_context(playwright)
            except ImportError:
                return {u: (False, None,
                            "playwright_unavailable: playwright-stealth is not installed")
                        for u in urls}
            except Exception as exc:  # noqa: BLE001 — browsers not installed, profile locked, ...
                return {u: (False, None,
                            f"playwright_unavailable: cannot launch firefox "
                            f"({type(exc).__name__}: {exc})"[:300]) for u in urls}

            async def _one(url: str) -> None:
                async with sem:
                    try:
                        html, final_url = await asyncio.wait_for(
                            _fetch_one(ctx, url, timeout_ms, block_resources),
                            timeout=ceiling_s)
                    except asyncio.TimeoutError:
                        results[url] = (
                            False, None,
                            f"playwright_timeout: no response within {ceiling_s:.0f}s")
                        return
                    except Exception as exc:  # noqa: BLE001 — a dead page is data
                        results[url] = (
                            False, None,
                            f"playwright_error: {type(exc).__name__}: {exc}"[:300])
                        return
                    results[url] = (True, {"html": html, "final_url": final_url}, None)

            try:
                await asyncio.gather(*(_one(u) for u in urls))
            finally:
                try:
                    await asyncio.wait_for(ctx.close(), timeout=CONTEXT_CLOSE_TIMEOUT_S)
                except Exception:  # noqa: BLE001 — a context that will not close is not a harvest failure
                    pass
    except Exception as exc:  # noqa: BLE001 — driver startup itself failed
        return {u: results.get(
            u, (False, None,
                f"playwright_unavailable: {type(exc).__name__}: {exc}"[:300]))
            for u in urls}

    # Belt and braces: `gather` above fills every key, but a caller must be able
    # to trust that the map is total.
    for url in urls:
        results.setdefault(url, (False, None, "playwright_error: no result"))
    return results


def fetch_html_batch(
    urls: list[str],
    *,
    parallel: int = DEFAULT_PARALLEL,
    timeout_ms: int = NAV_TIMEOUT_MS,
    block_resources: bool = True,
) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Synchronous facade for `fetch_html_batch_async`.

    `asyncio.run` rather than a reusable loop: the harvest calls this at most
    once per answer, and owning the loop for the duration keeps this module
    callable from ordinary synchronous driver code.
    """
    if not urls:
        return {}
    try:
        return asyncio.run(fetch_html_batch_async(
            urls, parallel=parallel, timeout_ms=timeout_ms,
            block_resources=block_resources))
    except RuntimeError as exc:
        # Already inside a running loop — the driver is synchronous, so this
        # means a caller changed, not that a URL failed.
        return {u: (False, None, f"playwright_unavailable: {exc}"[:200]) for u in urls}
