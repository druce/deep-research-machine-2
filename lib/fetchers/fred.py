#!/usr/bin/env python3
"""FRED macro series -> `data/_MACRO/structured/fred_<series>.json` (spec §12.1).

Macro evidence is shared across tickers, so it lives in the one `_MACRO` tree
and is cited from any ticker's pages (§12's two-step citation resolution).

The API key is appended at the request boundary and never recorded: `request`
carries the endpoint and the non-credential params only (§5, §11.1's API-key
rule). §8.4's secret scan is the backstop, not the mechanism.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_URL = "https://api.stlouisfed.org/fred/series"
HTTP_TIMEOUT = 30

# §12.1's frequency policy. A daily series goes stale in 2 days; an annual one
# not for well over a year, so refetching it nightly is pure waste.
FREQUENCY_POLICY_DAYS: dict[str, int] = {"D": 2, "W": 9, "M": 40, "Q": 100, "A": 400}

# §12.1: an unrecognized frequency gets the monthly policy AND a warning — the
# fetch still succeeds, but silently guessing would hide a provider change.
UNKNOWN_FREQUENCY_POLICY_DAYS = 40

# The `_meta` fields §12.1 requires be stored for every series.
SERIES_META_FIELDS = ("title", "units", "frequency_short", "seasonal_adjustment",
                      "last_updated", "realtime_start", "realtime_end")


def artifact_id(series_id: str) -> str:
    """`fred_<series_id_lower>` (§12.1)."""
    return f"fred_{series_id.lower()}"


def policy_for(frequency_short: str | None) -> tuple[dict, str | None]:
    """`(policy, warning)` for a FRED frequency code (§12.1)."""
    code = (frequency_short or "").strip().upper()[:1]
    if code in FREQUENCY_POLICY_DAYS:
        return {"policy_days": FREQUENCY_POLICY_DAYS[code]}, None
    return ({"policy_days": UNKNOWN_FREQUENCY_POLICY_DAYS},
            f"unknown FRED frequency {frequency_short!r}; "
            f"defaulting to {UNKNOWN_FREQUENCY_POLICY_DAYS}-day policy")


def _fred_get(url: str, params: dict) -> dict:
    """GET a FRED endpoint with the key appended, returning parsed JSON.

    Never raises an exception carrying the key: httpx builds its error messages
    from the full request URL, query string included, so the raw exception is
    swallowed and re-raised naming only the endpoint (§11.1, same rule as
    `lib/fmp_http.py`).
    """
    import httpx  # local import: keep the module importable offline

    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set")
    endpoint = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        resp = httpx.get(url, params={**params, "api_key": key, "file_type": "json"},
                         timeout=HTTP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — any transport error, key-free
        # `from None`: the chained httpx exception carries the keyed URL.
        raise RuntimeError(
            f"FRED {endpoint} request failed: {type(exc).__name__}") from None
    if resp.status_code >= 400:
        raise RuntimeError(f"FRED {endpoint} -> HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(f"FRED {endpoint} returned non-JSON") from None
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"FRED {endpoint} returned {type(payload).__name__}, expected an object")
    return payload


def _fred_provider(series_id: str) -> dict:
    """Default provider: series metadata plus its observations."""
    series = _fred_get(FRED_SERIES_URL, {"series_id": series_id})
    obs = _fred_get(FRED_OBSERVATIONS_URL, {"series_id": series_id})
    entries = series.get("seriess") or []
    return {"series": entries[0] if entries else {},
            "observations": obs.get("observations") or []}


def fetch_fred_series(
    series_id: str,
    macro_dir: Path,
    state: dict,
    *,
    series_provider: Callable[[str], dict] | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Fetch one FRED series into `_MACRO/structured/` with provenance (§12.1).

    Returns the usual `(success, paths, error_msg)`, where a warning (unknown
    frequency) rides on a successful result — §12.3 makes a failed macro series
    a warning at the `prefetch-macro` level, so an unusable series must never
    take the whole run down.
    """
    provider = series_provider or _fred_provider
    now = now or datetime.now(timezone.utc)
    try:
        raw = provider(series_id)
    except Exception as exc:  # provider errors are data, not crashes
        return False, [], f"FRED {series_id} fetch failed: {exc}"

    observations = [o for o in (raw.get("observations") or []) if isinstance(o, dict)]
    if not observations:
        return False, [], f"FRED {series_id} returned no observations"
    series_meta = raw.get("series") or {}

    policy, warning = policy_for(series_meta.get("frequency_short"))
    # §12.1: store the current vintage plus realtime_start/end and last_updated,
    # so a reader can tell which vintage a figure came from even though the
    # artifact itself is mutable by id (§28.6 covers versioning).
    extras = {f: series_meta.get(f) for f in SERIES_META_FIELDS}
    aid = artifact_id(series_id)
    meta = StructuredMeta(
        id=aid, ticker="_MACRO", producer="fetch",
        title=series_meta.get("title") or f"FRED {series_id}",
        source="FRED (Federal Reserve Bank of St. Louis)",
        url=f"https://fred.stlouisfed.org/series/{series_id}",
        provider_tool="api.stlouisfed.org/fred/series/observations",
        fetch_cmd=f"uv run python sra.py prefetch-macro --series {series_id.lower()}",
        fetched_at=now.isoformat(),
        # §6.4: as_of is the period end — the last observation, not the fetch time.
        as_of=str(observations[-1].get("date") or now.date().isoformat()),
        # The key is OMITTED, not blanked: §5 requires absence, and §8.4 rejects a
        # credential parameter here even when empty.
        request={"endpoint": FRED_OBSERVATIONS_URL, "params": {"series_id": series_id}},
    )
    path = write_structured(macro_dir, meta, {"series": extras,
                                              "observations": observations})
    record_fetch(state, aid, aid, now, policy)
    return True, [path], warning
