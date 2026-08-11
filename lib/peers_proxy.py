#!/usr/bin/env python3
"""Peers source 3: the DEF 14A compensation peer group, as prose.

We do NOT parse this. A regex anchored on CrowdStrike's "Fiscal 2026 Peer Group"
heading matched no other issuer -- PANW writes "2025 compensation peer group",
TOST "Annual Compensation Peer Group Review", TSLA "Tesla's peer group
companies" -- and it failed silently, contributing nothing while reporting
success. All four proxies do contain the literal words "peer group", so we
excerpt around those and hand the prose to a model, which also removes the
name-to-ticker resolution step that once picked BSQKZ (OTC) over XYZ (NYSE).

Remember what this source is: a group selected for the EXECUTIVE TALENT market
(CRWD's own criteria are revenue 0.5x-2.5x, market cap 0.25x-4x, high growth),
which is why it contains Snap and Roblox. The excerpt deliberately includes the
criteria so the reading model can weigh it accordingly.
"""
from __future__ import annotations

import re
from typing import Callable

PROXY_FORM = "DEF 14A"

# The one phrase every issuer uses, whatever their heading style.
PEER_PHRASE = re.compile(r"peer group", re.IGNORECASE)
WINDOW = 4000        # chars of context each side of a mention
MAX_EXCERPT = 60000  # hard cap; TSLA's proxy is 1.19M chars with many mentions
SEPARATOR = "\n\n[...]\n\n"


def extract_peer_excerpt(
    text: str,
    window: int = WINDOW,
    max_chars: int = MAX_EXCERPT,
) -> str:
    """Merged +/-`window` char spans around every "peer group" mention.

    Overlapping spans are merged so shared text is never emitted twice, and
    non-adjacent spans are joined by SEPARATOR so the reader can see that
    material was skipped. Returns "" when the phrase never appears.
    """
    spans: list[tuple[int, int]] = []
    for match in PEER_PHRASE.finditer(text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    if not spans:
        return ""
    parts = [" ".join(text[s:e].split()) for s, e in spans]
    return SEPARATOR.join(parts)[:max_chars]


def _latest_proxy(ticker: str) -> object | None:
    """Most recent DEF 14A via edgartools, or None when the issuer files none."""
    import edgar  # local import: edgartools sets a process-global SEC identity

    # Reuses the filings fetcher's identity setup rather than repeating the
    # SEC_FIRM/SEC_USER read: two copies would drift, and the failure mode is
    # SEC blocking the client with no useful degraded mode.
    from lib.fetchers.edgar import _init_edgar

    _init_edgar()
    filings = list(edgar.Company(ticker).get_filings(form=PROXY_FORM))
    return filings[0] if filings else None


def fetch_proxy_excerpt(
    ticker: str,
    filing_fn: Callable[[str], object] | None = None,
) -> tuple[str, str, str]:
    """(excerpt, filing_url, filing_date) from the latest DEF 14A.

    ("", "", "") when the issuer files no proxy or the proxy never says
    "peer group" -- both normal, neither an error.
    """
    filing_fn = filing_fn or _latest_proxy
    filing = filing_fn(ticker)
    if filing is None:
        return "", "", ""
    excerpt = extract_peer_excerpt(filing.text())
    if not excerpt:
        return "", "", ""
    return (excerpt,
            str(getattr(filing, "url", "")),
            str(getattr(filing, "filing_date", "")))
