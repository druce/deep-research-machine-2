"""The one FMP GET: no API key in any error, no error envelope coerced to [].

httpx builds HTTPStatusError's message from the full request URL, query string
included. Every peers endpoint puts `apikey` in `params`, and the resulting text
is stored in `warnings`, printed by `sra.py`, and relayed to the user by
`.claude/skills/sra-peers/SKILL.md`. These tests pin the error path.
"""
import httpx
import pytest

from lib.fmp_http import REDACTED, endpoint_name, fmp_get, sanitize

SECRET = "SUPERSECRETKEY"
URL = "https://financialmodelingprep.com/stable/profile"


def _keyed_request() -> httpx.Request:
    return httpx.Request("GET", f"{URL}?symbol=CRWD&apikey={SECRET}")


def test_httpx_really_does_leak_the_key_without_us():
    """The premise. If this ever stops being true the sanitization can go."""
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        httpx.Response(403, request=_keyed_request()).raise_for_status()
    assert SECRET in str(excinfo.value)


def test_sanitize_redacts_every_query_secret_spelling():
    text = (f"url 'https://x/y?symbol=CRWD&apikey={SECRET}&token={SECRET}"
            f"&api_key={SECRET}'")
    got = sanitize(text)
    assert SECRET not in got
    assert got.count(REDACTED) == 3


def test_sanitize_stops_at_the_problems_separator():
    """`fetch_peers` joins its problems with "; " -- redacting a secret at the
    end of one problem must not eat the delimiter before the next."""
    got = sanitize(f"FMP peers failed: url?apikey={SECRET}; DEF 14A excerpt failed: x")
    assert SECRET not in got
    assert got.endswith("; DEF 14A excerpt failed: x")


def test_a_403_carries_the_entitlement_reason_but_not_the_key(monkeypatch):
    """FMP puts the actionable reason in the 403 body ("Legacy Endpoint", plan tier)."""
    monkeypatch.setenv("FMP_API_KEY", SECRET)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        403, text=f"Exclusive Endpoint: upgrade (apikey={SECRET})",
        request=_keyed_request()))
    with pytest.raises(RuntimeError) as excinfo:
        fmp_get(URL, {"symbol": "CRWD"})
    assert "not entitled (403)" in str(excinfo.value)
    assert "Exclusive Endpoint" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


def test_a_401_never_carries_the_key(monkeypatch):
    """The live failure: an expired key or a plan-tier 403 on any endpoint."""
    monkeypatch.setenv("FMP_API_KEY", SECRET)
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: httpx.Response(401, request=_keyed_request()))
    with pytest.raises(RuntimeError) as excinfo:
        fmp_get(URL, {"symbol": "CRWD"})
    message = str(excinfo.value)
    assert SECRET not in message and "apikey" not in message
    assert "profile" in message and "401" in message


def test_a_transport_error_never_carries_the_key(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", SECRET)

    def boom(*a, **kw):
        raise httpx.ConnectError(f"failed to connect to {URL}?apikey={SECRET}")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(RuntimeError) as excinfo:
        fmp_get(URL, {"symbol": "CRWD"})
    assert SECRET not in str(excinfo.value)
    # and the chained exception, which a traceback would print, is dropped
    assert excinfo.value.__cause__ is None


def test_an_error_envelope_raises_instead_of_reading_as_no_data(monkeypatch):
    """FMP answers a throttled/unentitled call with HTTP 200 and a DICT.

    The old `if not isinstance(payload, list): return []` turned that provider
    failure into "the source returned nothing" -- exit 0, warnings null.
    """
    monkeypatch.setenv("FMP_API_KEY", SECRET)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json={"Error Message": "Limit Reach . Please upgrade your plan"},
        request=_keyed_request()))
    with pytest.raises(RuntimeError, match="expected a list"):
        fmp_get(URL, {"symbol": "CRWD"})


def test_a_non_json_body_raises_without_the_key(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", SECRET)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, text="<html>gateway</html>", request=_keyed_request()))
    with pytest.raises(RuntimeError, match="non-JSON") as excinfo:
        fmp_get(URL, {"symbol": "CRWD"})
    assert SECRET not in str(excinfo.value)


def test_the_key_is_appended_to_the_caller_s_params(monkeypatch):
    seen = {}
    monkeypatch.setenv("FMP_API_KEY", SECRET)

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"], seen["timeout"] = url, params, timeout
        return httpx.Response(200, json=[{"symbol": "CRWD"}], request=_keyed_request())

    monkeypatch.setattr(httpx, "get", fake_get)
    assert fmp_get(URL, {"symbol": "CRWD"}, timeout=7) == [{"symbol": "CRWD"}]
    assert seen == {"url": URL, "params": {"symbol": "CRWD", "apikey": SECRET},
                    "timeout": 7}


def test_missing_key_is_its_own_error(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FMP_API_KEY"):
        fmp_get(URL, {"symbol": "CRWD"})


def test_endpoint_name_is_the_last_path_segment():
    assert endpoint_name("https://financialmodelingprep.com/stable/etf/info") == "info"
