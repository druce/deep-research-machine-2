"""DEF 14A peer-group excerpting.

We no longer parse the proxy. A regex fitted to CrowdStrike's "Fiscal 2026 Peer
Group" heading matched no other company: PANW says "2025 compensation peer
group", TOST "Annual Compensation Peer Group Review", TSLA "Tesla's peer group
companies". All four contain the literal words "peer group", so we excerpt
around those and let a model read the prose.
"""
from pathlib import Path

from lib.peers_proxy import (
    MAX_EXCERPT,
    WINDOW,
    extract_peer_excerpt,
    fetch_proxy_excerpt,
)

FIXTURE = Path(__file__).parent / "fixtures" / "def14a_crwd_excerpt.txt"


def test_excerpt_captures_the_peer_group_list():
    got = extract_peer_excerpt(FIXTURE.read_text(encoding="utf-8"))
    for name in ("AppLovin Corporation", "Palo Alto Networks, Inc.",
                 "Zscaler, Inc.", "Shopify Inc."):
        assert name in got, name


def test_excerpt_captures_the_selection_criteria():
    """The criteria are what tell a model this is a talent-market cohort."""
    got = extract_peer_excerpt(FIXTURE.read_text(encoding="utf-8"))
    assert "0.5x" in got and "2.5x" in got


def test_excerpt_is_empty_when_the_phrase_never_appears():
    assert extract_peer_excerpt("A proxy about directors and audit fees.") == ""


def test_excerpt_merges_overlapping_windows_without_duplicating_text():
    """Two mentions 100 chars apart must not emit the span between them twice."""
    text = "x" * 5000 + " peer group AAA " + "y" * 100 + " peer group BBB " + "z" * 5000
    got = extract_peer_excerpt(text, window=4000)
    assert got.count("AAA") == 1
    assert got.count("BBB") == 1


def test_excerpt_separates_distant_windows_with_a_marker():
    text = "peer group ONE" + "q" * 50_000 + "peer group TWO"
    got = extract_peer_excerpt(text, window=100)
    assert "ONE" in got and "TWO" in got
    assert "[...]" in got
    assert "q" * 1000 not in got     # the 50k filler is not carried


def test_excerpt_respects_the_max_chars_cap():
    text = ("peer group " + "w" * 500) * 400
    got = extract_peer_excerpt(text, window=4000, max_chars=10_000)
    assert len(got) <= 10_000


def test_excerpt_collapses_whitespace():
    text = "peer group   Alpha\n\n\n   Beta"
    assert "peer group Alpha Beta" in extract_peer_excerpt(text, window=200)


def test_window_and_cap_have_the_documented_defaults():
    assert WINDOW == 4000 and MAX_EXCERPT == 60_000


def test_fetch_proxy_excerpt_threads_text_url_and_date():
    class _Filing:
        filing_date = "2026-05-05"
        url = "https://www.sec.gov/Archives/edgar/data/1535527/x-index.htm"

        def text(self):
            return FIXTURE.read_text(encoding="utf-8")

    excerpt, url, date = fetch_proxy_excerpt("CRWD", filing_fn=lambda t: _Filing())
    assert "AppLovin Corporation" in excerpt
    assert url.startswith("https://www.sec.gov/Archives/")
    assert date == "2026-05-05"


def test_fetch_proxy_excerpt_returns_empty_when_no_filing():
    assert fetch_proxy_excerpt("FOREIGNCO", filing_fn=lambda t: None) == ("", "", "")


def test_fetch_proxy_excerpt_returns_empty_when_phrase_absent():
    class _Filing:
        filing_date = "2026-01-01"
        url = "https://example.invalid/x"

        def text(self):
            return "No compensation benchmarking discussion at all."

    assert fetch_proxy_excerpt("X", filing_fn=lambda t: _Filing()) == ("", "", "")
