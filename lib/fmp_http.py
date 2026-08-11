#!/usr/bin/env python3
"""One GET for every FMP /stable/ endpoint, with the API key kept out of errors.

httpx builds `HTTPStatusError`'s message from the FULL request URL, query string
included -- and the peers pipeline puts `apikey` in `params`. A 401/403/429 on
any endpoint therefore produced a message carrying the live key, which
`fetch_peers` stores in `warnings`, `sra.py` prints to stdout, and
`.claude/skills/sra-peers/SKILL.md` tells the agent to relay to the user.

So no httpx message ever escapes this module: every failure is re-raised as a
RuntimeError naming the endpoint and the status, and any text we do carry
through (a provider's own error envelope) is passed through `sanitize` first.
`lib/fetchers/transcript.py:_fmp_get` had this shape for its 403 case; this is
that idea generalized and made the single call site for the six peers endpoints.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

HTTP_TIMEOUT = 30

# httpx's own query-value type: what may legally go into `params`.
QueryValue = str | int | float | bool | None

# Any `secret=value` pair that could appear in a URL, an httpx message, or a
# provider's own echo of the request. Matched case-insensitively; the value runs
# to the next separator or quote. `;` is in the excluded set because
# `lib/fetchers/peers.py` joins its problems with "; " -- redacting a secret at
# the end of one problem must not eat the delimiter before the next.
_SECRET_QS = re.compile(
    r"\b(apikey|api_key|apiKey|token|access_token|key)=[^&;\s\"'>]*", re.IGNORECASE)
REDACTED = "REDACTED"

# FMP answers a throttled or unentitled call with HTTP 200 and a DICT envelope
# ({"Error Message": "..."}), so the shape check below is a failure signal, not
# an empty result. Keep the message short: it is operator-facing, not a log dump.
_ENVELOPE_CHARS = 200


def sanitize(text: object) -> str:
    """Redact `apikey=...`-style query secrets from provider error text."""
    return _SECRET_QS.sub(rf"\1={REDACTED}", str(text))


def endpoint_name(url: str) -> str:
    """The last path segment, for error messages that must not carry the URL."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def fmp_get(url: str, params: Mapping[str, QueryValue],
            timeout: int = HTTP_TIMEOUT) -> list:
    """GET an FMP endpoint with the API key appended; return its JSON list.

    Raises RuntimeError -- never an httpx exception, and never a message
    containing the key -- when the key is unset, the request fails, the status
    is >= 400, the body is not JSON, or the payload is not the contracted list.
    A non-list payload is a PROVIDER FAILURE (FMP's error envelope is a dict),
    not "the source returned nothing", so it must not be coerced to [].

    403 carries a sanitized snippet of the body: FMP puts the reason there
    ("Legacy Endpoint", "Exclusive Endpoint", plan tier), and entitlement is the
    one HTTP failure an operator can actually act on.
    """
    import httpx  # local import: keep the module importable offline

    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise RuntimeError("FMP_API_KEY not set")
    name = endpoint_name(url)
    try:
        resp = httpx.get(url, params={**params, "apikey": key}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - any transport error, sanitized
        # `from None`: the chained httpx exception carries the keyed URL in its
        # own message, and a traceback would print it.
        raise RuntimeError(f"FMP {name} request failed: "
                           f"{type(exc).__name__}: {sanitize(exc)}") from None
    if resp.status_code == 403:
        raise RuntimeError(f"FMP {name} endpoint not entitled (403): "
                           f"{sanitize(resp.text[:_ENVELOPE_CHARS])}")
    if resp.status_code >= 400:
        raise RuntimeError(f"FMP {name} -> HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"FMP {name} returned non-JSON: {sanitize(exc)}") from None
    if not isinstance(payload, list):
        raise RuntimeError(
            f"FMP {name} returned {type(payload).__name__}, expected a list: "
            f"{sanitize(payload)[:_ENVELOPE_CHARS]}")
    return payload
