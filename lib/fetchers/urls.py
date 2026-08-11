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
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urljoin, urlsplit

# §8.3.1 / §8.3, pinned by tests.
MAX_REDIRECTS = 3
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_MARKDOWN_CHARS = 200_000
WEB_PAGE_POLICY_DAYS = 30

ALLOWED_SCHEMES = frozenset({"http", "https"})
MIME_ALLOWLIST = frozenset({
    "text/html", "text/plain", "application/pdf", "application/xhtml+xml"})

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
        root = lxml_html.fromstring(html)
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

            truncated = len(markdown) > MAX_MARKDOWN_CHARS
            if truncated:
                markdown = markdown[:MAX_MARKDOWN_CHARS]
            return True, {
                "markdown": markdown,
                "final_url": final_url,
                "content_type": mime,
                "truncated": truncated,
                "title": title,
            }, None

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
    if mime == "text/plain":
        return text.strip(), None
    return html_to_markdown(text)
