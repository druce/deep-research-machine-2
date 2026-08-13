#!/usr/bin/env python3
"""Tier 3 of the harvest failover chain: Bright Data's Web Unlocker.

The last resort for a URL that tier 1 (httpx) and tier 2 (headless Firefox) both
failed to retrieve. Web Unlocker dials the target from Bright Data's own residential
egress and solves the anti-bot challenge on their side, so it reaches hosts that a
browser running from our IP cannot — at a per-request cost, which is why it sits
last rather than first.

Like `browser_fetch`, this module is TRANSPORT ONLY: it returns HTML and lets
`lib/fetchers/urls.py` do the markdown extraction, so all three tiers agree about
what a page says and the import graph stays acyclic.

Two properties of this tier that the caller has to know about:

- **`final_url` is the input URL.** Web Unlocker does not report where redirects
  landed. `urls.py` recovers the real address from the document's
  `<link rel="canonical">` where there is one; there is nothing better available.
- **It cannot reach our private network.** The request originates from Bright
  Data, not from this host, so on the §8.3.1 axis this is the *safest* tier, not
  the most dangerous one. The SSRF concern here is only that a `final_url` we
  later record must still be a public address.

`brightdata-sdk` and `BRIGHTDATA_API_KEY` are both optional. Missing either one
makes every URL a named failure rather than an exception — a fallback tier that
throws when it is not configured is worse than no fallback at all.

API-key handling follows `lib/fmp_http.py` (spec §11.1): no exception text from
the SDK escapes this module unsanitized, because the SDK is free to build its
messages from the full request, and anything we return lands in a `warnings`
list that `sra.py` prints and a skill relays to the user.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable

from lib.fmp_http import REDACTED, sanitize

# Web Unlocker solves a challenge before it answers, so its latency is measured
# in tens of seconds, not the 20 that `urls.py` allows tier 1.
DEFAULT_TIMEOUT = 90
DEFAULT_CONCURRENCY = 6
# Provider messages are operator-facing; a full SDK traceback in a warnings list
# is noise that hides the one line that matters.
MAX_ERROR_CHARS = 300


def api_key() -> str | None:
    """The configured Bright Data token, or None. Never logged, never returned."""
    return (os.environ.get("BRIGHTDATA_API_KEY") or "").strip() or None


def zone() -> str | None:
    """The Web Unlocker zone to use, or None to let the SDK pick its default."""
    return (os.environ.get("BRIGHTDATA_ZONE") or "").strip() or None


def _redact(text: object, key: str | None) -> str:
    """Scrub provider text of both query-string secrets and the live token.

    `sanitize` handles the `token=...`/`apikey=...` shapes that appear in an
    echoed URL. It cannot catch a bare token quoted in an SDK message ("invalid
    credentials for abc123"), so the configured key is replaced literally too.
    """
    out = sanitize(text)
    if key:
        out = out.replace(key, REDACTED)
    return out[:MAX_ERROR_CHARS]


def _interpret(url: str, result: Any, key: str | None
               ) -> tuple[bool, dict | None, str | None]:
    """Map an SDK `ScrapeResult` onto the repo's `(success, data, error)` triple.

    Two failure modes are specific to this provider and neither raises: a job
    that finished in a state other than `ready`, and a `ready` job whose body is
    empty. A short-but-present body is NOT judged here — `urls.py` owns the
    thin-extract test, so that one rule lives in one place for all three tiers.
    """
    status = getattr(result, "status", None)
    if status and status != "ready":
        return False, None, f"brightdata_status: {_redact(status, key)}"

    data = getattr(result, "data", None)
    html = data if isinstance(data, str) else (str(data) if data else "")
    if not html:
        # `ScrapeResult.error` is often None even for an empty body — a host that
        # defeats Web Unlocker outright looks like a clean job that fetched
        # nothing. Carry the field when it IS set: it is the only diagnostic the
        # provider offers, and without it every such failure reads identically.
        detail = getattr(result, "error", None)
        suffix = f" ({_redact(detail, key)})" if detail else ""
        return False, None, (
            f"brightdata_empty: provider returned an empty body{suffix}")

    # `final_url` echoes the input; see the module docstring.
    return True, {"html": html, "final_url": url}, None


async def _scrape_one(client: Any, sem: asyncio.Semaphore, url: str,
                      zone_name: str | None, key: str | None
                      ) -> tuple[bool, dict | None, str | None]:
    """One Web Unlocker request, holding a concurrency slot for its duration."""
    async with sem:
        try:
            result = (await client.scrape_url(url, zone=zone_name) if zone_name
                      else await client.scrape_url(url))
        except Exception as exc:  # noqa: BLE001 — provider failures are data here
            return False, None, (
                f"brightdata_error: {_redact(f'{type(exc).__name__}: {exc}', key)}")
    return _interpret(url, result, key)


async def _scrape_many(urls: list[str], *, key: str, zone_name: str | None,
                       timeout: int, concurrency: int
                       ) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Run the whole batch through one async client under a semaphore."""
    from brightdata import BrightDataClient

    sem = asyncio.Semaphore(max(1, concurrency))
    async with BrightDataClient(
        token=key,
        timeout=timeout,
        web_unlocker_zone=zone_name,
        auto_create_zones=True,
        # The SDK's token pre-flight is a second network round trip that fails
        # the whole batch for a transient blip; a bad token shows up per-URL
        # anyway, which is where we want it.
        validate_token=False,
    ) as client:
        results = await asyncio.gather(
            *(_scrape_one(client, sem, u, zone_name, key) for u in urls))
    return dict(zip(urls, results))


def fetch_html_batch(
    urls: Iterable[str],
    *,
    key: str | None = None,
    zone_name: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Fetch every URL through Web Unlocker.

    Returns `{url: (success, {"html", "final_url"}, error)}`, total over the
    inputs: every URL handed in comes back as a key whatever happened to it.

    Nothing raises. An uninstalled SDK, an unset key, a provider error and a
    driver failure are all per-URL failures, so the caller's chain can treat
    "tier 3 is not available" and "tier 3 tried and lost" identically.
    """
    urls = list(dict.fromkeys(urls))  # de-dup, preserve order
    if not urls:
        return {}

    try:
        import brightdata  # noqa: F401  — presence check only
    except ImportError:
        return {u: (False, None,
                    "brightdata_unavailable: brightdata-sdk is not installed")
                for u in urls}

    key = key or api_key()
    if not key:
        return {u: (False, None,
                    "brightdata_unavailable: BRIGHTDATA_API_KEY is not set")
                for u in urls}

    zone_name = zone_name or zone()

    try:
        return asyncio.run(_scrape_many(
            urls, key=key, zone_name=zone_name, timeout=timeout,
            concurrency=concurrency))
    except RuntimeError as exc:
        # Already inside a running loop: a caller changed, not a URL failure.
        return {u: (False, None,
                    f"brightdata_unavailable: {_redact(exc, key)}") for u in urls}
    except Exception as exc:  # noqa: BLE001 — client construction itself failed
        return {u: (False, None,
                    f"brightdata_error: "
                    f"{_redact(f'{type(exc).__name__}: {exc}', key)}") for u in urls}
