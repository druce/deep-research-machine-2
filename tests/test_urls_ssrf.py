"""SSRF controls on the model-URL fetcher (spec §8.3.1, §24 "SSRF" block).

`fetch-urls` is the ONE component that fetches a URL a model chose, so it is the
one place where a prompt-injected or hallucinated address reaches the network.
Every control §8.3.1 lists is asserted here as an independent test: scheme
allowlist, pre-connection DNS resolution with denial of the non-public ranges,
re-validation at every redirect hop, hop cap, byte cap, MIME allowlist, and
truncation recording.

The network is never touched: `resolver` and `client` are both injected, so a
test that "resolves" a hostname to 10.0.0.5 or "serves" a 6 MB body is exact and
offline rather than dependent on someone's DNS.
"""
from __future__ import annotations

import httpx
import pytest

from lib.fetchers.urls import (
    MAX_BYTES,
    MAX_MARKDOWN_CHARS,
    MAX_REDIRECTS,
    MIME_ALLOWLIST,
    TIMEOUT_SECONDS,
    WEB_PAGE_POLICY_DAYS,
    FetchRejected,
    check_url_allowed,
    fetch_url_to_markdown,
)

PUBLIC_IP = "93.184.216.34"  # example.com


def resolver_for(mapping: dict[str, str]):
    """A `socket.getaddrinfo` stand-in returning `mapping[host]` in getaddrinfo shape."""
    def _resolve(host: str, port, *args, **kwargs):
        if host not in mapping:
            raise OSError(f"unknown host {host!r}")
        return [(2, 1, 6, "", (mapping[host], port or 80))]
    return _resolve


def public_resolver(host: str, port, *args, **kwargs):
    """Every hostname resolves to one public address."""
    return [(2, 1, 6, "", (PUBLIC_IP, port or 80))]


def client_serving(handler) -> httpx.Client:
    """An httpx.Client whose every request is answered by `handler` (no sockets)."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def html_response(body: str, *, content_type: str = "text/html; charset=utf-8"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": content_type}, text=body)
    return handler


# --- constants pinned by the spec -----------------------------------------

def test_spec_constants():
    """§8.3.1 numbers and §8.3's freshness window are pinned, not incidental."""
    assert MAX_REDIRECTS == 3
    assert MAX_BYTES == 5 * 1024 * 1024
    assert TIMEOUT_SECONDS == 20
    assert MAX_MARKDOWN_CHARS == 200_000
    assert WEB_PAGE_POLICY_DAYS == 30
    assert MIME_ALLOWLIST == frozenset({
        "text/html", "text/plain", "application/pdf", "application/xhtml+xml"})


# --- scheme allowlist ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/x",
    "data:text/html,<b>hi</b>",
    "javascript:alert(1)",
])
def test_rejects_non_http_schemes(url):
    """§8.3.1: only https and http. `file:` is the canonical §24 rejection."""
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed(url, resolver=public_resolver)
    assert exc.value.reason == "scheme_not_allowed"


def test_accepts_http_and_https():
    for url in ("http://example.com/a", "https://example.com/a"):
        assert check_url_allowed(url, resolver=public_resolver) == PUBLIC_IP


# --- literal-IP denials (no DNS involved) ---------------------------------

@pytest.mark.parametrize("url,reason", [
    ("http://127.0.0.1/x", "loopback"),
    ("http://127.1.2.3/x", "loopback"),
    ("http://[::1]/x", "loopback"),
    ("http://169.254.169.254/meta", "cloud_metadata"),
    ("http://[fd00:ec2::254]/meta", "cloud_metadata"),
    ("http://169.254.1.1/x", "link_local"),
    ("http://10.0.0.5/x", "private"),
    ("http://192.168.1.1/x", "private"),
    ("http://172.16.0.1/x", "private"),
    ("http://224.0.0.1/x", "multicast"),
    ("http://0.0.0.0/x", "unspecified"),
])
def test_rejects_non_public_literal_addresses(url, reason):
    """§8.3.1's denial list, asserted per range. The metadata address is checked
    explicitly rather than left to `is_link_local`, so its reason code names it."""
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed(url, resolver=public_resolver)
    assert exc.value.reason == reason


def test_rejects_ipv4_mapped_ipv6_loopback():
    """`::ffff:127.0.0.1` is loopback wearing an IPv6 hat — the mapped form must
    not route around the v4 checks."""
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed("http://[::ffff:127.0.0.1]/x", resolver=public_resolver)
    assert exc.value.reason == "loopback"


# --- DNS resolution BEFORE connection -------------------------------------

def test_rejects_hostname_resolving_to_private_ip():
    """§24: `DNS → private IP`. The name is public; only resolution reveals it."""
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed("http://internal.example.com/x",
                          resolver=resolver_for({"internal.example.com": "10.0.0.5"}))
    assert exc.value.reason == "private"


def test_rejects_when_any_resolved_address_is_private():
    """A host answering with one public and one private address is refused: the
    connection could land on either, so the public answer is not a safe pick."""
    def mixed(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (PUBLIC_IP, port)), (2, 1, 6, "", ("10.0.0.5", port))]
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed("http://mixed.example.com/x", resolver=mixed)
    assert exc.value.reason == "private"


def test_rejects_unresolvable_host():
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed("http://nope.example.com/x", resolver=resolver_for({}))
    assert exc.value.reason == "dns_failure"


def test_rejects_missing_host():
    with pytest.raises(FetchRejected) as exc:
        check_url_allowed("http:///justapath", resolver=public_resolver)
    assert exc.value.reason == "no_host"


def test_resolution_happens_before_any_connection():
    """The DNS check must gate the request, not annotate it after the fact."""
    connected: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        connected.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>x</p>")

    ok, data, err = fetch_url_to_markdown(
        "http://internal.example.com/x",
        client=client_serving(handler),
        resolver=resolver_for({"internal.example.com": "10.0.0.5"}))
    assert (ok, data) == (False, None)
    assert "private" in err
    assert connected == []  # never dialed


# --- redirect validation at EVERY hop -------------------------------------

def test_rejects_public_host_redirecting_to_private_ip():
    """§24: `public redirect → private IP`. The first hop is clean; the second
    is the attack, so validating only the URL we were handed is not enough."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://192.168.1.1/"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>x</p>")

    ok, data, err = fetch_url_to_markdown(
        "http://example.com/a", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "private" in err


def test_follows_a_permitted_redirect_and_records_the_final_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "http://example.com/b"})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><title>B</title><body><p>landed</p></body></html>")

    ok, data, err = fetch_url_to_markdown(
        "http://example.com/a", client=client_serving(handler), resolver=public_resolver)
    assert (ok, err) == (True, None)
    assert data["final_url"] == "http://example.com/b"
    assert "landed" in data["markdown"]


def test_rejects_more_than_three_redirects():
    """§8.3.1 caps hops at 3 — a redirect loop must terminate, not spin."""
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={"location": f"http://example.com/?n={n + 1}"})

    ok, data, err = fetch_url_to_markdown(
        "http://example.com/?n=0", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "too_many_redirects" in err


def test_rejects_redirect_to_a_non_http_scheme():
    """A `file:` Location is the same escape as a `file:` input URL."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    ok, data, err = fetch_url_to_markdown(
        "http://example.com/a", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "scheme_not_allowed" in err


def test_rejects_redirect_without_a_location_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    ok, data, err = fetch_url_to_markdown(
        "http://example.com/a", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "redirect_without_location" in err


# --- response cap ----------------------------------------------------------

def test_rejects_oversized_response_body():
    """§24: `oversized response`. Counted while streaming, so a 6 MB body is
    abandoned mid-flight rather than buffered and then measured."""
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [b"x" * (1024 * 1024)] * 6
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=iter(chunks))

    ok, data, err = fetch_url_to_markdown(
        "http://example.com/big", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "too_large" in err


def test_rejects_oversized_declared_content_length():
    """An honest `content-length` over the cap is refused before the body is read."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html",
                                            "content-length": str(MAX_BYTES + 1)},
                              content=b"")
    ok, data, err = fetch_url_to_markdown(
        "http://example.com/big", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "too_large" in err


# --- MIME allowlist --------------------------------------------------------

@pytest.mark.parametrize("content_type", [
    "application/zip", "application/octet-stream", "image/png",
    "application/json", "text/csv",
])
def test_rejects_content_types_outside_the_allowlist(content_type):
    ok, data, err = fetch_url_to_markdown(
        "http://example.com/x",
        client=client_serving(html_response("body", content_type=content_type)),
        resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "mime_not_allowed" in err


def test_accepts_text_plain():
    ok, data, err = fetch_url_to_markdown(
        "http://example.com/x",
        client=client_serving(html_response("just words", content_type="text/plain")),
        resolver=public_resolver)
    assert (ok, err) == (True, None)
    assert data["markdown"] == "just words"
    assert data["content_type"] == "text/plain"


def test_rejects_http_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, text="nope")
    ok, data, err = fetch_url_to_markdown(
        "http://example.com/x", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "http_404" in err


# --- the accept path -------------------------------------------------------

PAGE = """<html><head><title>Palo Alto Networks Q3</title>
<style>.x{color:red}</style><script>var a=1;</script></head>
<body><nav>skip me</nav><h1>Q3 Results</h1>
<p>Revenue grew 15% year over year.</p>
<p>Billings were up as well.</p></body></html>"""


def test_accepts_a_public_html_page():
    ok, data, err = fetch_url_to_markdown(
        "https://example.com/news", client=client_serving(html_response(PAGE)),
        resolver=public_resolver)
    assert (ok, err) == (True, None)
    assert data["title"] == "Palo Alto Networks Q3"
    assert data["final_url"] == "https://example.com/news"
    assert data["content_type"] == "text/html"
    assert data["truncated"] is False
    assert "Revenue grew 15% year over year." in data["markdown"]
    assert "Q3 Results" in data["markdown"]


def test_nested_blocks_do_not_run_together():
    """A parent's text_content() concatenates its children with no separator, so
    extracting per block element emitted `innersibling` beside the real ones.
    Each block's own text must appear exactly once, separated."""
    nested = ("<html><body><div><p>inner</p><p>sibling</p></div>"
              "<div>loose text<p>para</p></div></body></html>")
    _ok, data, _err = fetch_url_to_markdown(
        "https://example.com/x", client=client_serving(html_response(nested)),
        resolver=public_resolver)
    assert data["markdown"].split("\n\n") == ["inner", "sibling", "loose text", "para"]


def test_inline_markup_keeps_its_surrounding_spaces():
    """Tail text belongs to the parent's flow: dropping it fuses words together."""
    inline = "<html><body><p>Revenue grew <b>15%</b> year over year.</p></body></html>"
    _ok, data, _err = fetch_url_to_markdown(
        "https://example.com/x", client=client_serving(html_response(inline)),
        resolver=public_resolver)
    assert data["markdown"] == "Revenue grew 15% year over year."


def test_html_comments_are_not_extracted():
    commented = "<html><body><p>keep this</p><!-- INJECTED --></body></html>"
    _ok, data, _err = fetch_url_to_markdown(
        "https://example.com/x", client=client_serving(html_response(commented)),
        resolver=public_resolver)
    assert data["markdown"] == "keep this"


def test_script_and_style_text_is_not_extracted():
    """Script bodies are not prose; leaving them in poisons both the evidence and
    any later grep over it."""
    _ok, data, _err = fetch_url_to_markdown(
        "https://example.com/news", client=client_serving(html_response(PAGE)),
        resolver=public_resolver)
    assert "var a=1" not in data["markdown"]
    assert "color:red" not in data["markdown"]


def test_markdown_is_truncated_at_200k_and_flagged():
    """§8.3.1: truncation at 200k chars, and the fact of it is recorded so a
    reader of the source knows the document is partial."""
    big = "<html><body>" + ("<p>" + "word " * 40 + "</p>") * 2000 + "</body></html>"
    ok, data, _err = fetch_url_to_markdown(
        "https://example.com/long", client=client_serving(html_response(big)),
        resolver=public_resolver)
    assert ok
    assert len(data["markdown"]) == MAX_MARKDOWN_CHARS
    assert data["truncated"] is True


def test_transport_failure_is_a_clean_error_not_an_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")
    ok, data, err = fetch_url_to_markdown(
        "http://example.com/x", client=client_serving(handler), resolver=public_resolver)
    assert (ok, data) == (False, None)
    assert "transport_error" in err
