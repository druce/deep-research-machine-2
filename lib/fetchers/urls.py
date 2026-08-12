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
import json
import os
import re
import socket
import tempfile
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

    Returns `{"fetched": [url], "skipped": [url], "errors": {url: reason}}`.
    """
    fetch = fetcher or fetch_url_to_markdown
    now = now or datetime.now(timezone.utc)

    meta, _body = read_source(answer_path)
    ticker, artifact_id = meta.ticker, meta.id

    mapping = read_url_map(ticker_dir, artifact_id)
    result: dict = {"fetched": [], "skipped": [], "errors": {}}
    fetched = 0

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

        if max_n is not None and fetched >= max_n:
            break  # leave the rest unharvested for a later run

        fetched += 1
        ok, data, err = fetch(url)
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

    _write_url_map(ticker_dir, artifact_id, mapping)
    return result


def harvest_targets(ticker_dir: Path) -> list[Path]:
    """Every document with UNHARVESTED `cited_urls` (§8.3's no-`--from` case).

    "Unharvested" means a cited URL that is not yet a KEY in the document's map
    — so a URL whose fetch failed (`null`) is not retried on every bulk run,
    which would mean hammering a dead link forever. Naming the document with
    `--from` re-attempts those, since that is an explicit request.

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
    return targets
