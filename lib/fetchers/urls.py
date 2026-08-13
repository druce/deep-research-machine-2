#!/usr/bin/env python3
"""Model-selected URL fetching, hardened against SSRF (spec §8.3, §8.3.1).

This is the ONLY component in sra6 that fetches a URL a model chose. Every other
fetcher targets an endpoint the code names itself, so the address is a constant;
here the address arrives from a researcher answer or an aggregator's
`cited_urls`, which means it can be hallucinated, or injected into the model by
the very pages we read. That makes this module the entire attack surface for
"make the agent fetch something on the host's private network", so §8.3.1's
controls are not defense in depth — they are the only defense.

The controls, all mandatory:

- schemes: `https`/`http` only,
- DNS resolution BEFORE the connection, denying loopback, link-local, private,
  multicast, unspecified, and cloud-metadata addresses,
- re-validation at EVERY redirect hop (redirects are followed manually, with
  `follow_redirects=False`, precisely so each `Location` goes back through the
  same check),
- at most `MAX_REDIRECTS` hops,
- `MAX_BYTES` response cap, counted while streaming,
- `TIMEOUT_SECONDS` timeout,
- a MIME allowlist,
- markdown truncated at `MAX_MARKDOWN_CHARS`, with the truncation recorded.

`resolver` and `client` are injectable so all of the above is testable offline
and exactly (a hostname that "resolves" to 10.0.0.5 costs nothing to assert),
rather than dependent on live DNS.

Residual risk, deliberately accepted: validation resolves the name and then
httpx resolves it again when it dials, so a DNS rebinding attack with a
sub-second TTL could in principle return a public address to us and a private
one to the socket. Closing that would mean pinning the resolved IP into a custom
transport and carrying the Host header by hand, which the spec does not ask for;
the window is narrow and the attacker must already control the nameserver for a
host the model was induced to cite.

Second residual risk, from the failover ladder (`fetch_batch`), and named here
because an undocumented weakening of a stated control is worse than the
weakening itself: tiers 2 and 3 follow redirects INTERNALLY. A browser and a
proxy both chase `Location` themselves, and neither can be made to hand each hop
back for approval the way tier 1's manual loop does. So for those tiers the
per-hop guarantee above does NOT hold. What holds instead:

- the entry URL is validated before any tier is attempted, and
- the FINAL url — the one that would be recorded, slugged into an id, and cited
  — is validated again on the way out (`_from_html`).

An intermediate hop through a private address is therefore possible on tiers 2
and 3 where it is not on tier 1. The exposure is a blind request: the browser
runs in its own process with no repo credentials, the response is discarded
unless the final address is public, and tier 3 does not even originate on this
host — Bright Data dials from its own network, which makes it the tier LEAST
able to reach anything of ours. Escalation is also refused outright for every
§8.3.1 reason code (see `ESCALATABLE_REASONS`), so a URL rejected for pointing
at a private address is never retried on a heavier tier.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

from lib.provenance import (
    SourceMeta, _slug, make_source_id, read_source, resolve_source, write_source)

# §8.3.1 / §8.3, pinned by tests.
MAX_REDIRECTS = 3
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 20


def request_headers() -> dict[str, str]:
    """Headers for every harvest fetch, led by a declared identity.

    httpx defaults to `python-httpx/<version>`. sec.gov refuses that outright —
    its fair-access policy requires a declared identity — so EVERY sec.gov URL a
    researcher cited came back 403 and mapped to `null`, which makes the claim
    resting on it uncitable. The SPCX build lost its entire S-1 that way: the
    prospectus was the only source for revenue by geography, customer
    concentration and the executive table, and three separate agents reported it
    as an unfillable gap.

    The identity is the same `SEC_FIRM SEC_USER` pair `edgar.set_identity` uses,
    so one `.env` configures both paths. It is sent to every host, not just SEC:
    declaring who is asking is good manners everywhere, and a bare library UA is
    what most publishers' bot rules key on.
    """
    firm = os.environ.get("SEC_FIRM", "").strip()
    user = os.environ.get("SEC_USER", "").strip()
    identity = f"{firm} {user}".strip() or "sra6 (SEC_FIRM/SEC_USER unset)"
    return {
        "User-Agent": identity,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
# A 10-K's inline-XBRL text runs well past the old 200_000 cap, and the cut
# landed mid-MD&A: the TOST run stored six SEC filings that stop before Item 7A
# and the notes, which are precisely the parts a research question reaches a
# filing for. Seven of 122 harvested pages hit the cap. `MAX_BYTES` (5MB) already
# bounds what we will download, so this only bounds what we keep of it; 1.5M
# characters holds a full 10-K with room to spare and still refuses a runaway.
MAX_MARKDOWN_CHARS = 1_500_000

# Below this, a body is treated as evidence of a soft block rather than as a
# short page — see `_package`. 400 is the threshold `~/projects/newsagent` uses
# for the same purpose against the same class of publisher.
MIN_MARKDOWN_CHARS = 400

WEB_PAGE_POLICY_DAYS = 30

ALLOWED_SCHEMES = frozenset({"http", "https"})
# `application/json` is here because SEC's own data endpoints (data.sec.gov's
# companyconcept/companyfacts) serve it, and a researcher citing one had the
# fetch refused as `mime_not_allowed` — the TOST harvest lost three that way.
# JSON is stored verbatim through the `text/plain` path: it is prose enough to
# grep and quote, and reformatting it would make the stored bytes disagree with
# the endpoint they claim to be.
MIME_ALLOWLIST = frozenset({
    "text/html", "text/plain", "application/pdf", "application/xhtml+xml",
    "application/json"})

# Cloud instance-metadata endpoints, checked by literal address so the rejection
# names what it is. 169.254.169.254 (AWS/GCP/Azure/OpenStack) is inside
# link-local and would be caught anyway; fd00:ec2::254 (AWS IPv6) is inside a
# unique-local range that `ipaddress` reports as private, likewise. Naming them
# is what makes the reason code actionable in an audit.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254"})

Resolver = Callable[..., list]


class FetchRejected(Exception):
    """A URL was refused by an §8.3.1 control.

    Carries a machine-readable `reason` code (`scheme_not_allowed`, `private`,
    `loopback`, `cloud_metadata`, ...) alongside the message, so callers can
    branch on the control that fired and `fetch-urls` can record WHY a URL is
    absent from bronze rather than just that it is.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _classify(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """The denial reason for `ip`, or None if it is a public address.

    An IPv4-mapped IPv6 address (`::ffff:127.0.0.1`) is unwrapped first: its
    `is_loopback`/`is_private` flags describe the v6 wrapper, so a mapped
    loopback would otherwise walk straight past the v4 checks.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if str(ip) in METADATA_ADDRESSES:
        return "cloud_metadata"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"
    return None


def _reject_address(raw: str, url: str) -> str:
    """Return `raw` if it is a public address, else raise `FetchRejected`."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise FetchRejected("bad_address", f"{url}: {exc}") from None
    reason = _classify(ip)
    if reason is not None:
        raise FetchRejected(reason, f"{url} resolves to non-public address {raw}")
    return raw


def check_url_allowed(url: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    """Validate `url` against §8.3.1 and return the first resolved IP.

    Raises `FetchRejected` for a disallowed scheme, a missing host, a DNS
    failure, or ANY resolved address that is not public.

    Every address the host resolves to is checked, not just the one that would
    be dialed first: the connection may land on any of them (the resolver's
    order is not a promise), so a host answering with one public and one private
    address is refused outright rather than dialed hopefully.

    This runs BEFORE the connection — that ordering is the control. A check
    performed on the response would be an audit trail, not a defense.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchRejected(
            "scheme_not_allowed",
            f"{url}: scheme {scheme or '(none)'!r} is not one of "
            f"{sorted(ALLOWED_SCHEMES)}")

    try:
        host = parts.hostname
    except ValueError as exc:  # malformed IPv6 literal in the netloc
        raise FetchRejected("bad_address", f"{url}: {exc}") from None
    if not host:
        raise FetchRejected("no_host", f"{url}: no host component")

    port = parts.port or (443 if scheme == "https" else 80)

    # A literal address never goes to DNS: getaddrinfo would echo it back, and
    # relying on that round trip would make the control depend on the resolver.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _reject_address(host, url)

    try:
        infos = resolver(host, port)
    except Exception as exc:  # noqa: BLE001 — any resolver failure is a refusal
        raise FetchRejected(
            "dns_failure", f"{url}: cannot resolve {host!r} ({type(exc).__name__})"
        ) from None
    if not infos:
        raise FetchRejected("dns_failure", f"{url}: {host!r} resolved to nothing")

    addresses = [info[4][0] for info in infos]
    for address in addresses:
        _reject_address(address, url)
    return addresses[0]


# --- HTML -> markdown ------------------------------------------------------

def _extract_title(root) -> str | None:
    """The document's `<title>`, collapsed to one line, or None."""
    for node in root.iter("title"):
        text = " ".join((node.text_content() or "").split())
        if text:
            return text
    return None


_XML_DECLARATION_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def html_to_markdown(html: str) -> tuple[str, str | None]:
    """Extract readable text from `html` as `(markdown, title)`.

    lxml text extraction rather than a full HTML->markdown converter: what the
    downstream consumers need from a harvested page is its prose (it is grepped,
    quoted, and cited), and every converter that reproduces layout also
    reproduces navigation chrome and link soup. This mirrors the approach
    `sec_text_cleaner` takes on filing HTML.

    `script`, `style`, `noscript`, `template`, `svg` and comment nodes are
    dropped: their contents are not prose, and leaving them in would poison both
    the evidence and any later `grep` over it. Block-level structure is
    preserved only as paragraph breaks.
    """
    from lxml import etree, html as lxml_html  # local import: keep module importable

    try:
        # lxml refuses a str carrying an XML encoding declaration
        # ("Unicode strings with encoding declaration are not supported"), and
        # every modern SEC inline-XBRL filing is XHTML that opens with one. The
        # ValueError landed in the fallback below, so LMT's and NOC's 10-Qs were
        # stored as 200KB of raw Workiva markup — unreadable as evidence, and
        # tripping the secret scanner on the document GUIDs in their comments.
        root = lxml_html.fromstring(_XML_DECLARATION_RE.sub("", html, count=1))
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        # Not parseable as HTML (empty or malformed beyond recovery) — the raw
        # text is still better evidence than nothing.
        return " ".join(html.split()), None

    title = _extract_title(root)
    etree.strip_elements(
        root, "script", "style", "noscript", "template", "svg", "head",
        with_tail=False)
    etree.strip_elements(root, etree.Comment, with_tail=False)

    blocks: list[str] = []
    buffer: list[str] = []
    _collect_blocks(root, blocks, buffer)
    _flush(blocks, buffer)
    return "\n\n".join(blocks).strip(), title


# Elements that end the current run of text. `br`/`hr` are breaks rather than
# containers, but they separate text for the same reason and belong here.
_BLOCK_TAGS = frozenset({
    "p", "div", "li", "td", "th", "dd", "dt", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "article", "section",
    "main", "body", "header", "footer", "nav", "aside", "tr", "ul", "ol", "dl",
    "table", "br", "hr", "form", "fieldset", "address", "details", "summary",
})


def _flush(blocks: list[str], buffer: list[str]) -> None:
    """Move the accumulated inline text into `blocks` as one normalized paragraph."""
    text = " ".join("".join(buffer).split())
    if text:
        blocks.append(text)
    buffer.clear()


def _collect_blocks(node, blocks: list[str], buffer: list[str]) -> None:
    """Accumulate inline text into `buffer`, flushing at every block boundary.

    A depth-first walk rather than a `text_content()` per block element: a
    parent's `text_content()` repeats everything its children hold AND
    concatenates the fragments with no separator, so `<div><p>inner</p><p>sibling
    </p></div>` came out as the single run-together block `innersibling` next to
    the correct ones. Flushing at boundaries instead emits each block's own text
    exactly once, and keeps loose text in a mixed container (`<div>loose<p>para
    </p></div>`) rather than dropping it as a leaves-only rule would.

    `tail` text is appended after recursing into a child because it belongs to
    the PARENT's flow — that is what keeps `<p>Revenue grew <b>15%</b> year over
    year.</p>` from losing the space around the bold run.
    """
    is_block = isinstance(node.tag, str) and node.tag in _BLOCK_TAGS
    if is_block:
        _flush(blocks, buffer)
    if node.text:
        buffer.append(node.text)
    for child in node:
        if not isinstance(child.tag, str):  # comment/PI leftovers
            continue
        _collect_blocks(child, blocks, buffer)
        if child.tail:
            buffer.append(child.tail)
    if is_block:
        _flush(blocks, buffer)


def pdf_to_text(payload: bytes) -> str:
    """Extract text from a PDF body, or raise `FetchRejected`.

    §8.3.1 allows `application/pdf` through the MIME gate, but sra6 carries no
    PDF text extractor as a hard dependency. `pypdf` is used when it happens to
    be installed and the fetch is refused with a named reason otherwise — a
    documented degradation, so a PDF that cannot be turned into evidence shows
    up as an explicit `null` in the URL map rather than as a source containing
    binary noise.
    """
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        raise FetchRejected(
            "pdf_extraction_unavailable",
            "PDF text extraction needs `pypdf`, which is not installed") from None

    import io
    try:
        reader = PdfReader(io.BytesIO(payload))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 — a broken PDF is data, not a crash
        raise FetchRejected(
            "pdf_unreadable", f"cannot extract PDF text ({type(exc).__name__})"
        ) from None
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text:
        raise FetchRejected("pdf_no_text", "PDF contains no extractable text")
    return text


# --- the fetch itself ------------------------------------------------------

def _read_capped(response) -> bytes:
    """Stream `response` into memory, refusing anything over `MAX_BYTES`.

    The declared `content-length` is checked first (an honest oversized body is
    refused without reading it at all), and the running total is checked again
    per chunk, because `content-length` may be absent or a lie.
    """
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BYTES:
                raise FetchRejected(
                    "too_large",
                    f"declared content-length {declared} exceeds {MAX_BYTES} bytes")
        except ValueError:
            pass  # unparseable header: fall through to the streaming count

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_BYTES:
            raise FetchRejected(
                "too_large", f"response exceeds {MAX_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(payload: bytes, response) -> str:
    """Decode a text body using the response's charset, replacing bad bytes."""
    encoding = response.charset_encoding or "utf-8"
    try:
        return payload.decode(encoding, errors="replace")
    except LookupError:  # server named an encoding Python does not know
        return payload.decode("utf-8", errors="replace")


def fetch_url_to_markdown(
    url: str,
    *,
    client=None,
    resolver: Resolver = socket.getaddrinfo,
    min_chars: int = 0,
) -> tuple[bool, dict | None, str | None]:
    """Fetch `url` and return `(success, data, error)` (§8.3, §8.3.1).

    On success `data` is
    `{"markdown", "final_url", "content_type", "truncated", "title"}`.
    On refusal or failure the error string leads with the reason code.

    Redirects are followed BY HAND (`follow_redirects=False`) so every hop's
    `Location` passes back through `check_url_allowed` — an httpx-followed
    redirect chain would validate only the URL we started with, which is exactly
    the hole §24's "public redirect → private IP" case describes.

    Nothing raises: a rejected URL, a dead host, a 404 and an unparseable body
    are all data here, because §8.3 makes a failed target fetch a warning rather
    than a command failure.

    `client` and `resolver` are injectable for testing; when `client` is None a
    short-lived `httpx.Client` is created and closed here.
    """
    import httpx  # local import: keep the module importable offline

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)

    try:
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                check_url_allowed(current, resolver=resolver)
            except FetchRejected as exc:
                return False, None, str(exc)

            try:
                with client.stream("GET", current, follow_redirects=False,
                                   headers=request_headers(),
                                   timeout=TIMEOUT_SECONDS) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return False, None, (
                                f"redirect_without_location: {current} returned "
                                f"HTTP {response.status_code} with no Location")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        return False, None, (
                            f"http_{response.status_code}: {current}")

                    mime = response.headers.get(
                        "content-type", "").split(";")[0].strip().lower()
                    if mime not in MIME_ALLOWLIST:
                        return False, None, (
                            f"mime_not_allowed: {current} served "
                            f"{mime or '(none)'!r}")

                    payload = _read_capped(response)
                    final_url = str(response.url)
            except FetchRejected as exc:
                return False, None, str(exc)
            except httpx.HTTPError as exc:
                return False, None, (
                    f"transport_error: {current} ({type(exc).__name__}: {exc})")

            try:
                markdown, title = _to_markdown(payload, mime, response)
            except FetchRejected as exc:
                return False, None, str(exc)

            return _package(markdown, final_url, mime, title, min_chars)

        return False, None, (
            f"too_many_redirects: {url} exceeded {MAX_REDIRECTS} redirects")
    finally:
        if owns_client:
            client.close()


def _to_markdown(payload: bytes, mime: str, response) -> tuple[str, str | None]:
    """Turn an allowlisted body into `(markdown, title)`."""
    if mime == "application/pdf":
        return pdf_to_text(payload), None
    text = _decode(payload, response)
    if mime in ("text/plain", "application/json"):
        return text.strip(), None
    return html_to_markdown(text)


def _package(markdown: str, final_url: str, mime: str, title: str | None,
             min_chars: int = 0) -> tuple[bool, dict | None, str | None]:
    """Wrap one tier's extracted text as a result, applying the thin test.

    Every tier funnels through here, so "how long is too short" is decided once
    and identically for httpx, the browser and Bright Data.

    A body under `min_chars` comes back as `(False, data, "thin_content: ...")`
    — false, but WITH its data. That shape is deliberate: `fetch_batch` reads it
    as "try the next tier", and if no tier does better it accepts the longest
    thin body rather than dropping the URL. A genuinely short page is not a
    failure; a 404-shaped shell served with HTTP 200 is, and nothing at this
    level can tell them apart, so the judgement belongs to the ladder that can
    act on it.

    `min_chars` therefore defaults to 0 — OFF. A bare `fetch_url_to_markdown`
    keeps the §8.3 contract it has always had, where any 200 with an allowlisted
    body is a success; only the ladder opts in. Making the rule unconditional
    would turn every genuinely short page into a hard failure for callers with
    no second tier to fall back on.

    The TOST harvest stored two bodies at ZERO characters — one of them Toast's
    own Q2 2026 results release — as ordinary successes, and no reader
    downstream had any way to notice.

    A body that reads as a block page is refused the same way, whatever its
    length: `looks_like_block_page` catches the verbose ones that sail past
    `min_chars`.
    """
    truncated = len(markdown) > MAX_MARKDOWN_CHARS
    if truncated:
        markdown = markdown[:MAX_MARKDOWN_CHARS]
    data = {
        "markdown": markdown,
        "final_url": final_url,
        "content_type": mime,
        "truncated": truncated,
        "title": title,
    }
    if min_chars and looks_like_block_page(title, markdown):
        return False, data, (
            f"blocked_page: {final_url} returned "
            f"{(title or markdown[:60]).strip()!r}")
    if len(markdown) < min_chars:
        return False, data, (
            f"thin_content: {final_url} yielded {len(markdown)} chars of text")
    return True, data, None


# Titles that mean "this is not the document, it is the wall in front of it".
# Every one of these was observed standing in for real evidence in the TOST
# harvest — fintel served a 9,301-character "enhanced security screening" page
# and tipranks an 8,628-character 404, both of which clear any length threshold
# and would have been stored, cited, and quoted as fact.
_BLOCK_TITLE_MARKERS = (
    "access denied", "access to this page has been denied",
    "page not found", "404 - page not found", "error 404",
    "just a moment", "are you a robot", "attention required",
    "security screening", "enhanced security", "captcha",
    "authentication failed", "please enable javascript", "request blocked",
    "forbidden", "too many requests", "rate limit",
)

# Matched against the whole body only when it is short enough that the phrase
# IS the page. A long article may legitimately quote "access denied".
_BLOCK_BODY_SCAN_CHARS = 300


def looks_like_block_page(title: str | None, markdown: str) -> bool:
    """Is this an interstitial — a bot wall, a 404, a login gate — not a document?

    A length test alone cannot answer this: the block pages that actually cost
    the TOST run evidence were VERBOSE, carrying a publisher's full navigation
    chrome around a one-line refusal. Nor can length be dropped in favour of
    this, since the shortest walls carry no marker at all ("Powered and
    protected by Privacy", 33 characters). The two rules catch different halves.

    Deliberately conservative — it only refuses a page, and a false positive
    costs one citable source, so the markers are phrases that a real financial
    article would not carry in its TITLE.
    """
    haystack = (title or "").strip().lower()
    if any(marker in haystack for marker in _BLOCK_TITLE_MARKERS):
        return True
    if len(markdown) <= _BLOCK_BODY_SCAN_CHARS:
        body = markdown.strip().lower()
        return any(marker in body for marker in _BLOCK_TITLE_MARKERS)
    return False


# --- the failover ladder ---------------------------------------------------
#
# One httpx GET loses about a third of what a research phase cites, and almost
# none of that loss is a dead link. The TOST prefetch harvest recorded 76
# failures out of 202 URLs; the failing hosts were investing.com,
# businesswire.com, seekingalpha.com, paymentsdive.com and their neighbours,
# every one of which answers a bare HTTP client with 403 and a real browser with
# the article. A URL that fails here is not merely missing: `harvest_answer`
# writes it into the map as `null`, and §8.3 makes a `null` mean "this claim
# cannot be cited", so the synthesizer downstream must drop whatever rested on
# it. Losing a third of the evidence is therefore a research-quality problem,
# not a plumbing inconvenience.
#
# So: three tiers, cheapest first, and escalation only for failures a different
# transport could plausibly fix.

# Reasons worth retrying with a heavier tier. An allowlist rather than a
# denylist, so an unfamiliar reason code stays put instead of quietly earning a
# browser launch. `http_*` statuses are matched separately.
ESCALATABLE_REASONS = frozenset({
    "transport_error",          # connection reset / read timeout — often a block
    "too_many_redirects",       # a consent-wall bounce a browser walks through
    "redirect_without_location",
    "thin_content",             # HTTP 200 with an empty shell: the classic soft block
    "blocked_page",             # a bot wall / 404 / login gate served as HTTP 200
    # A heavier tier failing on its OWN terms — not installed, browser would not
    # launch, navigation died, the proxy returned a bad job. These say nothing
    # about the URL, only about that tier, so the next tier still deserves a go.
    # Omitting them is what made an unconfigured tier 2 swallow every URL before
    # tier 3 was ever consulted.
    "playwright_unavailable", "playwright_error", "playwright_timeout",
    "brightdata_unavailable", "brightdata_error", "brightdata_status",
    "brightdata_empty",
    "tier_error",
})

# Reasons that must NEVER escalate, and why, because getting this wrong is how a
# security control becomes decorative:
#   - every §8.3.1 refusal (scheme, private/loopback/metadata address, DNS): the
#     whole point of the control is that we do not dial these, and "try again
#     through a browser" is precisely the bypass it exists to stop;
#   - `too_large`: the body was refused on size, and no tier changes that;
#   - `mime_not_allowed`: a browser renders ANY content type into HTML, so
#     escalating would store a viewer shell as if it were the document;
#   - the `pdf_*` reasons: same, but worse — Firefox's PDF viewer chrome is not
#     the PDF's text.

MAX_TIER_ERROR_CHARS = 400

# What the driver uses. Modest on purpose: the ladder makes a slow URL slower
# (three transports instead of one), so the win is in overlapping them, not in
# hammering a publisher from twenty sockets at once.
DEFAULT_HARVEST_PARALLEL = 6

# EDGAR's fair-access policy caps automated traffic, and the whole-corpus harvest
# now runs concurrently, so SEC hosts get their own narrow lane. Everything else
# is bounded only by the pool.
PER_HOST_CONCURRENCY = {"sec.gov": 2}


def _reason_of(error: str) -> str:
    """The machine-readable code a tier error leads with."""
    return error.split(":", 1)[0].strip()


def is_escalatable(error: str | None) -> bool:
    """Should a URL that failed with `error` be retried on a heavier tier?"""
    if not error:
        return False
    reason = _reason_of(error)
    if reason in ESCALATABLE_REASONS:
        return True
    # `http_403`, `http_429`, `http_503`, ... — a status a different client or a
    # different egress may simply not receive. 404 is included deliberately:
    # publishers serve 404 to clients they dislike as readily as 403.
    return reason.startswith("http_")


def _host_semaphores(urls: list[str]) -> dict[str, threading.Semaphore]:
    """One semaphore per rate-limited host present in `urls`."""
    out: dict[str, threading.Semaphore] = {}
    for suffix, limit in PER_HOST_CONCURRENCY.items():
        if any(_matches_host(u, suffix) for u in urls):
            out[suffix] = threading.Semaphore(limit)
    return out


def _matches_host(url: str, suffix: str) -> bool:
    """Is `url`'s host `suffix` or a subdomain of it?"""
    host = (urlsplit(url).hostname or "").lower()
    return host == suffix or host.endswith(f".{suffix}")


def canonical_url(html: str, fallback: str) -> str:
    """The document's same-site `<link rel="canonical">`, else `fallback`.

    Bright Data's Web Unlocker does not report where redirects landed — it
    echoes the URL we asked for — so for tier 3 this is the only way to learn a
    page's real address.

    A canonical pointing at a DIFFERENT registrable host is ignored. A hostile
    or merely careless publisher would otherwise get to choose the `url` we
    record and the id we slug from it, which is how one document ends up filed
    under another document's name.
    """
    try:
        from lxml import html as lxml_html
        root = lxml_html.fromstring(_XML_DECLARATION_RE.sub("", html, count=1))
    except Exception:  # noqa: BLE001 — unparseable HTML just has no canonical
        return fallback

    for node in root.iter("link"):
        rel = (node.get("rel") or "").strip().lower()
        href = (node.get("href") or "").strip()
        if rel != "canonical" or not href:
            continue
        candidate = urljoin(fallback, href)
        if site_name(candidate) == site_name(fallback):
            return candidate
    return fallback


def _from_html(url: str, html: str, final_url: str
               ) -> tuple[bool, dict | None, str | None]:
    """Turn a tier-2/tier-3 HTML payload into a result.

    Extraction goes through the same `html_to_markdown` tier 1 uses, so the
    three tiers cannot disagree about what a page says.

    `final_url` is re-validated here because tiers 2 and 3 follow redirects
    internally: neither can be made to re-check each hop the way tier 1's manual
    redirect loop does, so the address we would RECORD is checked instead. See
    the module docstring's residual-risk note.
    """
    try:
        check_url_allowed(final_url)
    except FetchRejected as exc:
        return False, None, f"redirect_to_{exc.reason}: {exc.message}"

    markdown, title = html_to_markdown(html)
    return _package(markdown, final_url, "text/html", title, MIN_MARKDOWN_CHARS)


def _browser_tier(urls: list[str]) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Tier 2: headless Firefox, as a batch sharing one browser."""
    from lib.fetchers import browser_fetch

    out: dict[str, tuple[bool, dict | None, str | None]] = {}
    for url, (ok, data, err) in browser_fetch.fetch_html_batch(urls).items():
        out[url] = (_from_html(url, data["html"], data["final_url"])
                    if ok and data else (False, None, err))
    return out


def _brightdata_tier(urls: list[str]) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Tier 3: Bright Data Web Unlocker, with the canonical-URL recovery."""
    from lib.fetchers import brightdata_fetch

    out: dict[str, tuple[bool, dict | None, str | None]] = {}
    for url, (ok, data, err) in brightdata_fetch.fetch_html_batch(urls).items():
        if not ok or not data:
            out[url] = (False, None, err)
            continue
        html = data["html"]
        out[url] = _from_html(url, html, canonical_url(html, data["final_url"]))
    return out


TierOne = Callable[..., tuple[bool, dict | None, str | None]]
TierBatch = Callable[[list[str]], dict[str, tuple[bool, dict | None, str | None]]]


def _run_tier_one(urls: list[str], fetch: TierOne, parallel: int
                  ) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Every URL through tier 1, `parallel` at a time.

    `min_chars` is passed as a keyword so the thin-content rule applies to the
    REAL tier 1 without being imposed on an injected stand-in, which may
    legitimately serve short fixtures.

    `parallel == 1` runs a plain in-order loop rather than a one-worker pool, so
    the default path keeps the exact call ORDER the sequential implementation
    had. Several tests assert on that order, and more importantly a deterministic
    default is what makes a failed harvest reproducible.
    """
    if parallel <= 1:
        return {url: fetch(url, min_chars=MIN_MARKDOWN_CHARS) for url in urls}

    semaphores = _host_semaphores(urls)

    def _one(url: str) -> tuple[bool, dict | None, str | None]:
        held = [s for suffix, s in semaphores.items() if _matches_host(url, suffix)]
        for sem in held:
            sem.acquire()
        try:
            return fetch(url, min_chars=MIN_MARKDOWN_CHARS)
        finally:
            for sem in held:
                sem.release()

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return dict(zip(urls, pool.map(_one, urls)))


def fetch_batch(
    urls: list[str],
    *,
    tier1: TierOne | None = None,
    tier2: TierBatch | None = None,
    tier3: TierBatch | None = None,
    parallel: int = 1,
) -> dict[str, tuple[bool, dict | None, str | None]]:
    """Fetch `urls` through the failover ladder; one result per input URL.

    Phase-batched rather than a per-URL ladder, and that is the load-bearing
    choice: a browser launch costs seconds and hundreds of megabytes, so
    escalating one URL at a time from inside a thread pool would mean one
    Firefox per worker. Instead everything tier 1 lost is handed to tier 2 at
    once, and everything tier 2 lost to tier 3.

    A thin or blocked body is never stored. The original design here accepted
    the longest thin body as a last resort, on the theory that a genuinely short
    page should not be dropped for resembling a soft block. Replaying TOST's 63
    failed URLs through the ladder refuted that: of the bodies that stayed thin
    at every tier, ALL were bot walls, 404s or auth failures — "Powered and
    protected by Privacy", "Access to this page has been denied",
    "Authentication failed" — and not one was a real short article. Storing them
    would put a publisher's refusal into bronze under a plausible id, where a
    writer can cite it as evidence about the company.

    A `null` says the claim is not citable, which is exactly true. So the last
    resort is failure, and the merged per-tier reasons say what was tried.

    Every tier is injectable, which is what lets the whole ladder be tested
    without a network, a browser or a provider account.
    """
    urls = list(dict.fromkeys(urls))
    if not urls:
        return {}

    tier1 = tier1 or fetch_url_to_markdown
    tier2 = _browser_tier if tier2 is None else tier2
    tier3 = _brightdata_tier if tier3 is None else tier3

    results: dict[str, tuple[bool, dict | None, str | None]] = {}
    errors: dict[str, list[str]] = {url: [] for url in urls}
    pending = urls

    for stage in (lambda batch: _run_tier_one(batch, tier1, parallel), tier2, tier3):
        if not pending:
            break
        staged = stage(list(pending))
        still_pending: list[str] = []
        for url in pending:
            ok, data, err = staged.get(
                url, (False, None, "tier_error: no result returned"))
            if ok and data is not None:
                results[url] = (True, data, None)
                continue
            if err:
                errors[url].append(err)
            if is_escalatable(err):
                still_pending.append(url)
        pending = still_pending

    for url in urls:
        if url in results:
            continue
        joined = " | ".join(errors[url]) or "fetch failed"
        results[url] = (False, None, joined[:MAX_TIER_ERROR_CHARS])
    return results


# --- harvest (§8.3) --------------------------------------------------------
#
# Turning an answer's `cited_urls` into bronze is what lets a claim cite its
# ORIGIN rather than the aggregator that repeated it (§8.2). The map written
# alongside each answer is the handoff: the synthesizer reads it to rewrite
# answer-level URL citations as bronze ids, and a `null` there is the signal
# that a claim is NOT citable and must be dropped or re-sourced.

MANIFEST_NAME = "00_manifest.md"

# The slug is only a human-readable handle — `make_source_id` guarantees
# uniqueness with a `_<n>` suffix — so it is capped rather than made injective.
MAX_SLUG_CHARS = 60


def site_name(url: str) -> str:
    """The `source` field for a harvested page: its hostname, `www.` stripped."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def url_slug(url: str) -> str:
    """A filename-safe topic for `make_source_id`, from the host and path."""
    parts = urlsplit(url)
    host = site_name(url)
    slug = _slug(f"{host} {parts.path}")[:MAX_SLUG_CHARS].strip("-")
    return slug or "page"


def _parse_ts(value: str) -> datetime | None:
    """Parse a frontmatter `fetched_at`, treating a naive stamp as UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iter_current_sources(ticker_dir: Path):
    """Every CURRENT bronze document, manifest excluded, oldest id first.

    `sources/archive/` is deliberately skipped: a superseded copy is not the
    live evidence, and handing its id back would point a fresh citation at a
    document `manifest` and `grep` exclude by design (§5, §9).
    """
    for path in sorted((ticker_dir / "sources").glob("*.md")):
        if path.name == MANIFEST_NAME:
            continue
        try:
            meta, _ = read_source(path)
        except (KeyError, ValueError, OSError):
            continue  # unreadable/hand-edited: `validate` reports it, not us
        yield meta


def find_source_by_url(ticker_dir: Path, url: str) -> SourceMeta | None:
    """The newest current bronze source whose frontmatter `url` is `url`, else None.

    Kind-agnostic on purpose: §8.3's rule is "if the URL already exists in
    bronze", and if an aggregator's own URL is cited we already hold that
    document — refetching it as a `web_page` would be a second copy of the same
    evidence under a different id.
    """
    matches = [meta for meta in _iter_current_sources(ticker_dir) if meta.url == url]
    return matches[-1] if matches else None


def is_fresh(meta: SourceMeta, now: datetime) -> bool:
    """Was `meta` fetched within `WEB_PAGE_POLICY_DAYS` of `now`? (§8.3)

    An unparseable `fetched_at` counts as stale: the safe reading of "we cannot
    tell how old this is" is to refetch, not to cite it indefinitely.
    """
    fetched_at = _parse_ts(meta.fetched_at)
    if fetched_at is None:
        return False
    return now - fetched_at <= timedelta(days=WEB_PAGE_POLICY_DAYS)


def map_path(ticker_dir: Path, artifact_id: str) -> Path:
    """Where `<artifact-id>`'s URL→id map lives (§8.3).

    Aggregator sources get their map here too, next to the answers': §8.3 names
    exactly one location for the map, and the synthesizer looks up one id at a
    time without caring whether the citing document was an answer or a roundup.
    """
    return ticker_dir / "derived" / "answers" / f"{artifact_id}.urls.json"


def read_url_map(ticker_dir: Path, artifact_id: str) -> dict[str, str | None]:
    """The existing URL→id map for `artifact_id`, or `{}`."""
    path = map_path(ticker_dir, artifact_id)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_url_map(ticker_dir: Path, artifact_id: str,
                   mapping: dict[str, str | None]) -> Path:
    """Write the map atomically, in §8.3's bare `{url: id|null}` format.

    No `_meta` wrapper: §8.3 fixes the file's shape, and `validate` skips
    `*.urls.json` for that reason.
    """
    target = map_path(ticker_dir, artifact_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{artifact_id}.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def _write_web_page(ticker_dir: Path, ticker: str, artifact_id: str,
                    data: dict, now: datetime, supersedes: str | None) -> str:
    """Write one harvested page to `sources/` and return its id."""
    final_url = data["final_url"]
    sid = make_source_id("web_page", now.date(), url_slug(final_url),
                         ticker_dir=ticker_dir)
    meta = SourceMeta(
        id=sid, ticker=ticker, kind="web_page",
        source=site_name(final_url) or "web",
        url=final_url, fetched_at=now.isoformat(), as_of=now.date().isoformat(),
        title=data.get("title") or final_url,
        fetch_tool="lib/fetchers/urls.py",
        fetch_cmd=f"uv run python sra.py fetch-urls {ticker} --from {artifact_id}",
        supersedes=supersedes,
        # Harvested pages carry no `cited_urls` of their own: harvesting is one
        # hop from a researcher's citation, not a crawl. Populating this would
        # make the next `fetch-urls` follow links out of pages we just fetched.
        truncated=bool(data.get("truncated")))
    write_source(ticker_dir, meta, data["markdown"], today=now.date())
    return sid


def harvest_answer(
    ticker_dir: Path,
    answer_path: Path,
    max_n: int | None = None,
    *,
    fetcher: Callable[..., tuple[bool, dict | None, str | None]] | None = None,
    tier2: TierBatch | None = None,
    tier3: TierBatch | None = None,
    parallel: int = 1,
    now: datetime | None = None,
) -> dict:
    """Harvest one document's `cited_urls` into bronze (§8.3).

    `answer_path` is any frontmattered document carrying `cited_urls` — a
    researcher answer under `derived/answers/`, or an aggregator source
    (news, perplexity_research) under `sources/`. Both are read with
    `read_source`, and both get their map at `map_path`.

    Per URL, in order:

    1. already mapped to a still-resolvable id -> skip (this is what makes a
       rerun free);
    2. already in bronze and fresh (`WEB_PAGE_POLICY_DAYS`) -> reuse that id;
    3. otherwise fetch. A stale `web_page` is superseded; a stale source of any
       OTHER kind is left alone and a fresh `web_page` is written beside it,
       because superseding e.g. a `news` document with a `web_page` would break
       that fetcher's own `find_prior_source` chain.

    `max_n` caps NETWORK FETCHES, not URLs considered — a reuse costs nothing,
    so it should not consume the budget. URLs past the cap are left OUT of the
    map entirely rather than written as `null`: `null` means "attempted and not
    citable", and recording it for a URL never tried would keep any later run
    from picking it up.

    A failed fetch is a `null` entry plus an `errors` entry, never an exception
    (§8.3: a failed target fetch is a warning). Raises only if `answer_path`
    itself cannot be read — the one condition §8.3 makes fatal.

    `fetcher` is tier 1 of the ladder; `tier2`/`tier3` are the heavier tiers and
    default to the real browser and proxy. All three are injectable so the whole
    harvest is testable without a network.

    `parallel` defaults to 1, which runs tier 1 as a plain in-order loop. The
    driver raises it; the library keeps the deterministic default it has always
    had, so a harvest can be reproduced exactly when something goes wrong.

    Returns
    `{"fetched": [url], "skipped": [url], "errors": {url: reason},
      "truncated": [url]}`, where `truncated` lists pages stored but cut at
    `MAX_MARKDOWN_CHARS`.
    """
    fetch = fetcher or fetch_url_to_markdown
    now = now or datetime.now(timezone.utc)

    meta, _body = read_source(answer_path)
    ticker, artifact_id = meta.ticker, meta.id

    mapping = read_url_map(ticker_dir, artifact_id)
    # `truncated` is reported, not merely recorded on the source: a partial
    # capture that nothing surfaces is worse than a failed one, because it is
    # cited with full confidence. Six of TOST's SEC filings stopped mid-MD&A and
    # no output said so.
    result: dict = {"fetched": [], "skipped": [], "errors": {}, "truncated": []}

    # Phase 1 — decide, without touching the network. Every skip/reuse case is
    # settled here, and `prior` is resolved for the rest, so the fetch phase has
    # no filesystem questions left to ask. Safe to do up front because nothing
    # is WRITTEN until phase 3, so no lookup can be invalidated by our own work.
    plan: list[tuple[str, SourceMeta | None]] = []
    seen: set[str] = set()
    for url in meta.cited_urls:
        if url in seen:
            continue
        seen.add(url)

        existing = mapping.get(url)
        if existing and resolve_source(ticker_dir, existing) is not None:
            result["skipped"].append(url)
            continue

        prior = find_source_by_url(ticker_dir, url)
        if prior is not None and is_fresh(prior, now):
            mapping[url] = prior.id
            result["skipped"].append(url)
            continue

        if max_n is not None and len(plan) >= max_n:
            break  # leave the rest unharvested for a later run

        plan.append((url, prior))

    # Phase 2 — fetch, through the failover ladder, `parallel` at a time.
    fetched_results = fetch_batch(
        [url for url, _prior in plan],
        tier1=fetch, tier2=tier2, tier3=tier3, parallel=parallel)

    # Phase 3 — write, strictly in the original URL order and on ONE thread.
    # `make_source_id` derives uniqueness by scanning `sources/`, so concurrent
    # writers would race on its `_<n>` suffix and make ids depend on timing.
    for url, prior in plan:
        ok, data, err = fetched_results.get(
            url, (False, None, "fetch failed"))
        if not ok or data is None:
            mapping[url] = None
            result["errors"][url] = err or "fetch failed"
            continue

        supersedes = prior.id if prior is not None and prior.kind == "web_page" else None
        try:
            mapping[url] = _write_web_page(
                ticker_dir, ticker, artifact_id, data, now, supersedes)
        except (OSError, ValueError, FileExistsError) as exc:
            mapping[url] = None
            result["errors"][url] = f"write failed: {type(exc).__name__}: {exc}"
            continue
        result["fetched"].append(url)
        if data.get("truncated"):
            result["truncated"].append(url)

    _write_url_map(ticker_dir, artifact_id, mapping)
    return result


def harvest_targets(ticker_dir: Path, *, retry_failed: bool = False) -> list[Path]:
    """Every document with UNHARVESTED `cited_urls` (§8.3's no-`--from` case).

    "Unharvested" means a cited URL that is not yet a KEY in the document's map
    — so a URL whose fetch failed (`null`) is not retried on every bulk run,
    which would mean hammering a dead link forever. Naming the document with
    `--from` re-attempts those, since that is an explicit request.

    `retry_failed` widens the sweep to documents whose only outstanding URLs are
    those `null`s. The default rule assumes a `null` means the link is dead, and
    for a single-tier fetcher that was near enough; with the failover ladder in
    place most historical `null`s are OUR failure — a 403 to httpx that a
    browser walks straight through — so a corpus harvested before the ladder
    existed can be recovered without hand-writing a `--from` per answer.

    Both researcher answers and aggregator sources are scanned: §5's `cited_urls`
    exists on a news roundup for exactly this reason.
    """
    targets: list[Path] = []
    answers_dir = ticker_dir / "derived" / "answers"
    candidates = sorted(answers_dir.glob("*.md")) if answers_dir.is_dir() else []
    candidates += sorted(p for p in (ticker_dir / "sources").glob("*.md")
                         if p.name != MANIFEST_NAME)

    for path in candidates:
        try:
            meta, _ = read_source(path)
        except (KeyError, ValueError, OSError):
            continue
        if not meta.cited_urls:
            continue
        mapping = read_url_map(ticker_dir, meta.id)
        if any(url not in mapping for url in meta.cited_urls):
            targets.append(path)
        elif retry_failed and any(mapping.get(url) is None
                                  for url in meta.cited_urls):
            targets.append(path)
    return targets
