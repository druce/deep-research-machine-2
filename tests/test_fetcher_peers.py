"""The peers fetcher: four independent sources merged into one table (§13).

Ported from EXP with the sra6 layer deltas: every artifact is SILVER under
`derived/peers/` (§4.2, §13.3), never `structured/` and never a `.tmp/` scratch
dir; the shaped ones carry §6.2 producer shapes; and `peers_user.json` is a
BARE record, because a list the user typed has neither a url nor an antecedent
artifact and no producer shape describes it honestly.

Most of these tests exist because a peer source failed SILENTLY at some point:
`/etf/holder` 404ing, `/stable/profile` answering `200 []` to a comma-batched
request, `/etf/info` 403ing for every fund. Each one reported success and wrote
a table that looked fine, so the assertions here are mostly about a failure
being VISIBLE rather than about the happy path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lib.fetchers.peers import (
    FMP_EXPOSURE_URL,
    FMP_PEERS_URL,
    _fmp_exposure,
    _fmp_fund_holdings,
    _fmp_peers,
    fetch_peers,
    peers_path,
    read_user_peers,
)
from lib.peers_funds import fmp_etf_holdings_count
from lib.provenance import read_structured
from lib.statefile import init_state
from lib.validate import validate

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def art(ticker_dir: Path, name: str) -> Path:
    """`derived/peers/<name>.json` — the one home for every peers artifact."""
    return peers_path(ticker_dir, name)


def read_art(ticker_dir: Path, name: str):
    return read_structured(art(ticker_dir, name))


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


def _stub_providers():
    """Offline stand-ins for every network provider fetch_peers takes."""
    exposure = [
        {"etf_symbol": "SPAM", "weight": 0.1233},
        {"etf_symbol": "VCLO", "weight": 0.1078},
        {"etf_symbol": "VIRS", "weight": 0.1646},
        {"etf_symbol": "CRWL", "weight": 2.0000},      # leveraged, must drop
        {"etf_symbol": "CIBR.L", "weight": 0.0957},    # foreign, must drop
    ]
    holdings = {
        "VIRS": [{"symbol": "MRNA", "weight": 0.05}, {"symbol": "CRWD", "weight": 0.16}],
        "SPAM": [{"symbol": "PANW", "weight": 0.09}, {"symbol": "ZS", "weight": 0.03},
                 {"symbol": "CRWD", "weight": 0.12}],
        "VCLO": [{"symbol": "PANW", "weight": 0.08}, {"symbol": "ZS", "weight": 0.02},
                 {"symbol": "CRWD", "weight": 0.11}],
    }
    profiles = {
        s: {"symbol": s, "companyName": f"{s} Inc.", "sector": "Technology",
            "industry": "Software - Infrastructure", "country": "US",
            "exchange": "NASDAQ", "marketCap": 1_000, "description": f"{s} does things.",
            "isEtf": False, "isFund": False, "isActivelyTrading": True}
        for s in ("CRWD", "PANW", "ZS", "NET", "FTNT", "MRNA")
    }
    return {
        "exposure_provider": lambda t: exposure,
        "holdings_provider": lambda f: holdings.get(f, []),
        "info_provider": lambda f: {"VIRS": 56, "SPAM": 36, "VCLO": 31}.get(f),
        "proxy_provider": lambda t: (
            "…considered the following peer group selection criteria… "
            "Fiscal 2026 Peer Group Fortinet, Inc. Zscaler, Inc.",
            "https://www.sec.gov/Archives/x.htm", "2026-05-05"),
        # ONE symbol per call, never a comma-joined list: /stable/profile answers
        # a batched request with HTTP 200 and [], so a stub that split on ","
        # passed identically against the batched implementation that shipped
        # that bug and against the fixed one.
        "profile_provider": lambda symbol: ([profiles[symbol]]
                                            if symbol in profiles else []),
        # 4 quarters (fetch_revenues' TTM_QUARTERS) summing to the round number
        # the candidate-table test asserts on.
        "income_provider": lambda s: [{"symbol": s, "revenue": 250_000}] * 4,
    }


# --- the user source (bare, per §6.2 having no shape that fits) -------------

def test_user_peers_are_written_as_a_bare_record(tmp_ticker_dir: Path):
    """§13.3 requires the artifact; §6.2 has no producer shape for a typed list,
    so it carries no `_meta` and asserts no provenance it does not have."""
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_peers(
        "PANW", tmp_ticker_dir, state,
        user_peers=["crwd", "ZS", "FTNT", "S", "OKTA", "CHKP"],
        peers_provider=lambda t: [], now=NOW, **_stub_providers())
    assert ok, err
    payload = json.loads(art(tmp_ticker_dir, "peers_user").read_text(encoding="utf-8"))
    assert "_meta" not in payload
    assert payload["peers"][0] == {"symbol": "CRWD", "origin": "user_provided"}
    assert all(row["origin"] == "user_provided" for row in payload["peers"])
    assert payload["recorded_at"] == NOW.isoformat()


def test_user_peers_are_persisted_into_state(tmp_ticker_dir: Path):
    """§13.5: state is what a later refresh reconstructs the pinned list from."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_peers("PANW", tmp_ticker_dir, state, user_peers=["crwd", "ZS"],
                peers_provider=lambda t: [], now=NOW, **_stub_providers())
    selected = state["derived"]["peers_selected"]
    assert selected["user_peers"] == ["CRWD", "ZS"]
    assert selected["asked_at"] == NOW.isoformat()


def test_read_user_peers_round_trips(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_peers("PANW", tmp_ticker_dir, state, user_peers=["CRWD", "ZS"],
                peers_provider=lambda t: [], now=NOW, **_stub_providers())
    assert read_user_peers(tmp_ticker_dir) == (["CRWD", "ZS"], NOW.isoformat())


def test_read_user_peers_on_a_missing_file(tmp_ticker_dir: Path):
    assert read_user_peers(tmp_ticker_dir) == ([], None)


# --- the gather records every source independently -------------------------

def test_fmp_records_its_full_list_independently(tmp_ticker_dir: Path):
    """FMP is a voter, not a top-up: no cap, and no dedup against the user list.

    A peer BOTH the user and FMP named must appear in both artifacts so the
    merged row records sources == ["user", "fmp_peers"]. Deduping FMP against
    the user here would hide the agreement the overlap filter keys off.
    """
    state = init_state(tmp_ticker_dir, "PANW")
    raw = ["PANW", "CRWD", "ZS", "FTNT", "S", "OKTA", "CHKP", "QLYS", "TENB"]
    # No fund overlap here: the stub fund holdings include CRWD, which would add
    # a third "funds" source and mask the two-source assertion this test is about.
    stubs = _stub_providers()
    stubs["exposure_provider"] = lambda t: []
    ok, _paths, err = fetch_peers(ticker_dir=tmp_ticker_dir, ticker="PANW",
                                  state=state, user_peers=["CRWD", "NET"],
                                  peers_provider=lambda t: raw, now=NOW, **stubs)
    assert ok, err
    user, _stamp = read_user_peers(tmp_ticker_dir)
    _, fmp = read_art(tmp_ticker_dir, "peers_fmp")
    assert user == ["CRWD", "NET"]
    fmp_syms = [r["symbol"] for r in fmp["peers"]]
    assert "PANW" not in fmp_syms          # the subject is still excluded
    assert "CRWD" in fmp_syms              # but a user peer is NOT deduped away
    assert len(fmp_syms) == 8              # uncapped: everything FMP returned
    _, cand = read_art(tmp_ticker_dir, "peers_candidates")
    crwd = next(r for r in cand["candidates"] if r["symbol"] == "CRWD")
    assert crwd["sources"] == ["user", "fmp_peers"]


def test_no_user_peers_pure_fmp(tmp_ticker_dir: Path):
    """No user peers -> FMP list is written uncapped, subject excluded."""
    state = init_state(tmp_ticker_dir, "PANW")
    raw = ["PANW", "CRWD", "ZS", "FTNT"] + [f"P{i:02d}" for i in range(20)]
    ok, _paths, err = fetch_peers("PANW", tmp_ticker_dir, state,
                                  peers_provider=lambda t: raw, now=NOW,
                                  **_stub_providers())
    assert ok, err
    meta, data = read_art(tmp_ticker_dir, "peers_fmp")
    syms = [r["symbol"] for r in data["peers"]]
    assert "PANW" not in syms
    assert syms[:3] == ["CRWD", "ZS", "FTNT"]
    assert len(syms) == 23   # uncapped: everything FMP returned, minus the subject
    assert all(r["origin"] == "fmp" for r in data["peers"])
    assert not art(tmp_ticker_dir, "peers_user").exists()
    assert "apikey" not in meta.url
    assert state["data"]["peers_candidates"]["policy_days"] == 90


def test_gather_records_peers_candidates_never_peers(tmp_ticker_dir: Path):
    """§13.3: the gather selects nothing, so it must not stamp the `peers` kind —
    that would make `status` report a peer set that was never chosen, and
    `prefetch --stale-only` skip peers for 90 days."""
    state = init_state(tmp_ticker_dir, "PANW")
    fetch_peers("PANW", tmp_ticker_dir, state, user_peers=["CRWD"],
                peers_provider=lambda t: [], now=NOW, **_stub_providers())
    assert state["data"]["peers_candidates"]["current_ids"] == ["peers_candidates"]
    assert "peers" not in state["data"]


def test_provider_error_with_user_peers_keeps_user_file(tmp_ticker_dir: Path):
    """FMP down but user peers present -> user file written, error reported."""
    state = init_state(tmp_ticker_dir, "PANW")

    def down(t):
        raise RuntimeError("FMP_API_KEY not set")

    ok, _paths, err = fetch_peers("PANW", tmp_ticker_dir, state,
                                  user_peers=["CRWD", "NET"],
                                  peers_provider=down, now=NOW, **_stub_providers())
    assert ok
    assert err is not None and "FMP_API_KEY" in err
    assert art(tmp_ticker_dir, "peers_user").exists()


def test_fetch_peers_empty_fails(tmp_ticker_dir: Path):
    """§13.5: failure only when every source failed AND no user peers."""
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _, err = fetch_peers("PANW", tmp_ticker_dir, state,
                             peers_provider=lambda t: [],
                             exposure_provider=lambda t: [],
                             proxy_provider=lambda t: ("", "", ""),
                             now=NOW)
    assert not ok
    assert "no peers" in err


def test_fetch_peers_provider_error_no_user_peers(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")

    def boom(t):
        raise RuntimeError("FMP_API_KEY not set")

    ok, _, err = fetch_peers("PANW", tmp_ticker_dir, state, peers_provider=boom,
                             exposure_provider=lambda t: [],
                             proxy_provider=lambda t: ("", "", ""), now=NOW)
    assert not ok and "FMP_API_KEY" in err


# --- the artifacts themselves ---------------------------------------------

def test_fetch_peers_writes_every_artifact_under_derived_peers(tmp_ticker_dir: Path):
    """§4.2/§13.3: all silver, all in derived/peers/, never structured/."""
    state = init_state(tmp_ticker_dir, "CRWD")
    ok, _paths, err = fetch_peers(
        "CRWD", tmp_ticker_dir, state, user_peers=["NET"],
        peers_provider=lambda t: ["PANW", "FTNT"], now=NOW, **_stub_providers())
    assert ok, err
    for artifact in ("peers_user", "peers_fmp", "peers_proxy",
                     "peers_funds", "peers_candidates"):
        assert art(tmp_ticker_dir, artifact).exists(), artifact
    assert not list((tmp_ticker_dir / "structured").glob("peers_*.json"))
    assert not (tmp_ticker_dir / ".tmp").exists()


def test_gathered_artifacts_pass_validate(tmp_ticker_dir: Path):
    """The producer shapes are real: §8.4 reads them back off disk."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, user_peers=["NET"],
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    assert _errors(tmp_ticker_dir) == []


def test_source_artifacts_carry_fetch_shape(tmp_ticker_dir: Path):
    """Each mechanical source is one provider's answer: `fetch`, with the query
    recorded and the credential absent (§5, §6.2)."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, peers_provider=lambda t: ["PANW"],
                now=NOW, **_stub_providers())
    for name in ("peers_fmp", "peers_funds", "peers_proxy"):
        meta, _ = read_art(tmp_ticker_dir, name)
        assert meta.producer == "fetch", name
        assert meta.url and "apikey" not in meta.url, name
        assert meta.fetch_cmd == "uv run python sra.py peers-candidates CRWD"
        assert "apikey" not in json.dumps(meta.request or {})


def test_candidate_table_carries_compute_shape(tmp_ticker_dir: Path):
    """It is merged from the sources it names, so `compute`: no url, and a
    non-empty `derived_from` (§6.2)."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, user_peers=["NET"],
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    meta, _ = read_art(tmp_ticker_dir, "peers_candidates")
    assert meta.producer == "compute"
    assert not meta.url
    assert set(meta.derived_from) >= {"peers_user", "peers_fmp",
                                      "peers_proxy", "peers_funds"}


def test_proxy_artifact_carries_prose_not_symbols(tmp_ticker_dir: Path):
    """§13.2: no deterministic name extraction — the model reads the prose."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state,
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    meta, data = read_art(tmp_ticker_dir, "peers_proxy")
    assert "peer group" in data["excerpt"].lower()
    assert data["filing_date"] == "2026-05-05"
    assert meta.url.startswith("https://www.sec.gov/")
    _, cand = read_art(tmp_ticker_dir, "peers_candidates")
    # Fortinet is named in the excerpt but the proxy contributes no symbols,
    # so it is only a candidate if another source named it.
    ftnt = [r for r in cand["candidates"] if r["symbol"] == "FTNT"]
    assert not ftnt or "proxy" not in ftnt[0]["sources"]


def test_a_proxy_filing_without_a_url_still_writes(tmp_ticker_dir: Path):
    """`fetch` requires a non-empty url; a filing exposing none still has a
    canonical EDGAR home, which beats losing the excerpt entirely."""
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    stubs["proxy_provider"] = lambda t: ("a peer group excerpt", "", "2026-05-05")
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW"], now=NOW, **stubs)
    assert ok, err
    meta, _ = read_art(tmp_ticker_dir, "peers_proxy")
    assert meta.url.startswith("https://www.sec.gov/")
    assert _errors(tmp_ticker_dir) == []


def test_candidate_table_has_the_designed_columns(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, user_peers=["NET"],
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    _meta, data = read_art(tmp_ticker_dir, "peers_candidates")
    assert data["subject"] == "CRWD"
    assert [f["symbol"] for f in data["funds_used"]] == ["VIRS", "SPAM", "VCLO"]
    rows = {r["symbol"]: r for r in data["candidates"]}
    assert rows["CRWD"]["is_subject"] is True
    # fund_counts.pop(subject, None): CRWD is a top holding of all three stub
    # funds, but the subject must never count as its own fund-peer.
    assert rows["CRWD"]["fund_count"] == 0 and rows["CRWD"]["sources"] == []
    assert rows["PANW"]["fund_count"] == 2
    assert rows["PANW"]["fmp_industry"] == "Software - Infrastructure"
    assert rows["PANW"]["description"].startswith("PANW")
    assert rows["PANW"]["revenue_ttm"] == 1_000_000


def test_the_table_never_labels_fmp_taxonomy_as_gics(tmp_ticker_dir: Path):
    """§13.3: FMP's taxonomy is not GICS, and the real scheme is licensed data."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, peers_provider=lambda t: ["PANW"],
                now=NOW, **_stub_providers())
    text = art(tmp_ticker_dir, "peers_candidates").read_text(encoding="utf-8")
    assert "gics" not in text.lower()
    assert "fmp_sector" in text and "fmp_industry" in text


def test_overlap_filter_reaches_the_written_table(tmp_ticker_dir: Path):
    """MRNA is held only by VIRS and named by nobody -> must not survive."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state,
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    _, data = read_art(tmp_ticker_dir, "peers_candidates")
    assert "MRNA" not in {r["symbol"] for r in data["candidates"]}
    assert "ZS" in {r["symbol"] for r in data["candidates"]}   # 2 funds


# --- a failing source is visible, never silent ----------------------------

def test_a_failing_source_is_reported_but_does_not_fail_the_run(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()

    def dead_proxy(t):
        raise RuntimeError("EDGAR 503")

    stubs["proxy_provider"] = dead_proxy
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW"], now=NOW, **stubs)
    assert ok
    assert err is not None and "EDGAR 503" in err
    assert not art(tmp_ticker_dir, "peers_proxy").exists()
    meta, _ = read_art(tmp_ticker_dir, "peers_candidates")
    assert "peers_proxy" not in meta.derived_from


def test_no_apikey_leaks_into_any_meta_url(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, user_peers=["NET"],
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    for path in (tmp_ticker_dir / "derived" / "peers").glob("peers_*.json"):
        if path.name == "peers_user.json":
            continue          # bare by design: no _meta to read
        meta, _ = read_structured(path)
        assert "apikey" not in (meta.url or "")


def test_no_apikey_leaks_into_the_warnings_string(tmp_ticker_dir: Path, monkeypatch):
    """The leak was on the ERROR path, which nothing pinned.

    `warnings` is printed to stdout by `sra.py` and the skill relays it, so an
    expired key or a plan-tier 403 put the live key in the chat transcript. Here
    the real default provider runs against a real 401.
    """
    import httpx

    secret = "SUPERSECRETKEY"
    request = httpx.Request("GET", f"{FMP_PEERS_URL}?symbol=CRWD&apikey={secret}")
    monkeypatch.setenv("FMP_API_KEY", secret)
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: httpx.Response(401, request=request))
    state = init_state(tmp_ticker_dir, "CRWD")
    # no peers_provider: the default _fmp_peers runs, and 401s
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  user_peers=["NET"], now=NOW, **_stub_providers())
    assert ok
    assert secret not in err and "apikey" not in err
    assert "401" in err


def test_total_etf_info_failure_is_reported_not_swallowed(tmp_ticker_dir: Path):
    """/etf/info is a separate endpoint and can fail for every fund.

    select_funds treats each failure as "unknown holdings count" and skips the
    fund, so the whole source used to vanish with ok=True, warnings=None.
    """
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()

    def dead_info(symbol: str) -> int:
        raise RuntimeError("FMP info -> HTTP 403")

    stubs["info_provider"] = dead_info
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW"], now=NOW, **stubs)
    assert ok                                     # other sources carried the run
    assert not art(tmp_ticker_dir, "peers_funds").exists()
    assert err is not None
    assert "/etf/info lookups failed" in err      # and it says HOW MANY
    assert "3 of 3" in err
    meta, _ = read_art(tmp_ticker_dir, "peers_candidates")
    assert "peers_funds" not in meta.derived_from


def test_an_empty_fund_source_is_distinguishable_from_a_failed_one(tmp_ticker_dir: Path):
    """"Found nothing" must not read like "broke"."""
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    stubs["info_provider"] = lambda f: 9_999      # every fund is a broad-market fund
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW"], now=NOW, **stubs)
    assert ok
    assert "reported a holdings count inside the 20-150 band" in err
    assert "lookups failed" not in err


def test_an_etf_info_endpoint_answering_with_nothing_reads_as_a_failure(
        tmp_ticker_dir: Path, monkeypatch):
    """`200 []` from /etf/info for every fund — the /etf/holder 404 failure mode
    wearing a 200 — was reported as "no fund fell inside the band"."""
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json=[]))
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    stubs["info_provider"] = fmp_etf_holdings_count      # the real parser
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW"], now=NOW, **stubs)
    assert ok
    assert "/etf/info lookups failed" in err
    assert "holdings count inside" not in err


def test_an_fmp_peers_call_that_returns_nothing_is_reported(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "CRWD")
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: [], now=NOW,
                                  **_stub_providers())
    assert ok
    assert "FMP peers returned no symbols" in err


def test_a_null_holding_symbol_never_becomes_a_none_candidate(tmp_ticker_dir: Path):
    """FMP's holdings feed carries a null `asset` on cash/derivative lines.

    `str(None).strip().upper()` is the literal string "NONE", which cleared the
    overlap filter (2 funds) and the hygiene filter (all flags unknown) and
    reached the rater as a fabricated ticker.
    """
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    stubs["holdings_provider"] = lambda f: {
        "SPAM": [{"symbol": None, "weight": 0.9}, {"symbol": "PANW", "weight": 0.09}],
        "VCLO": [{"symbol": None, "weight": 0.9}, {"symbol": "PANW", "weight": 0.08}],
        "VIRS": [{"symbol": "PANW", "weight": 0.07}],
    }.get(f, [])
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: [], now=NOW, **stubs)
    assert ok, err
    _, cand = read_art(tmp_ticker_dir, "peers_candidates")
    symbols = {r["symbol"] for r in cand["candidates"]}
    assert "NONE" not in symbols
    assert "PANW" in symbols


# --- enrichment failures are visible too ----------------------------------

def test_total_enrichment_failure_is_reported_not_swallowed(tmp_ticker_dir: Path):
    """fetch_profiles/fetch_revenues degrade every failed lookup to "unknown"
    and fetch_peers never asked how many came back.

    With /stable/profile and /stable/income-statement 401ing for every candidate,
    the run reported success and wrote a table whose classification, size,
    revenue and description are all null — while hygiene_filter went inert
    because every flag was unknown.
    """
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()

    def dead(symbol: str) -> list[dict]:
        raise RuntimeError("FMP profile -> HTTP 401")

    stubs["profile_provider"] = dead
    stubs["income_provider"] = dead
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW", "ZS"],
                                  now=NOW, **stubs)
    assert ok
    assert err is not None
    assert "no profile data for any candidate" in err
    assert "lookups failed" in err            # and it says how many
    assert "no income data for any candidate" in err
    _, cand = read_art(tmp_ticker_dir, "peers_candidates")
    assert all(r["fmp_industry"] is None for r in cand["candidates"])


def test_enrichment_that_answers_with_nothing_is_reported_too(tmp_ticker_dir: Path):
    """The other door to the same room: /stable/profile answering `200 []` to a
    comma-joined batch raised nothing at all — which is how the bug shipped."""
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    stubs["profile_provider"] = lambda s: []
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW", "ZS"],
                                  now=NOW, **stubs)
    assert ok
    assert "no profile data for any candidate" in err
    assert "answered with no data" in err
    assert "lookups failed" not in err        # nothing raised; do not claim it did


def test_a_partial_enrichment_failure_is_counted(tmp_ticker_dir: Path):
    """One flaky symbol still degrades to "unknown" — but visibly."""
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    good = stubs["profile_provider"]

    def flaky(symbol: str):
        if symbol == "ZS":
            raise RuntimeError("FMP profile -> HTTP 429")
        return good(symbol)

    stubs["profile_provider"] = flaky
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW", "ZS"],
                                  now=NOW, **stubs)
    assert ok
    assert "1 of" in err and "profile lookups failed" in err
    _, cand = read_art(tmp_ticker_dir, "peers_candidates")
    rows = {r["symbol"]: r for r in cand["candidates"]}
    assert rows["PANW"]["fmp_industry"] == "Software - Infrastructure"
    assert rows["ZS"]["fmp_industry"] is None


def test_malformed_enrichment_payloads_do_not_traceback(tmp_ticker_dir: Path):
    """The enrich helpers claim "Never raises"; the parsing was outside the try
    and escaped `fetch_peers`, tracebacking out of `sra.py peers-candidates`
    instead of returning an exit code."""
    state = init_state(tmp_ticker_dir, "CRWD")
    stubs = _stub_providers()
    stubs["profile_provider"] = lambda s: ["not a dict"]
    stubs["income_provider"] = lambda s: [{"revenue": "1,234"}] * 4
    ok, _paths, err = fetch_peers("CRWD", tmp_ticker_dir, state,
                                  peers_provider=lambda t: ["PANW"], now=NOW, **stubs)
    assert ok, err
    _, cand = read_art(tmp_ticker_dir, "peers_candidates")
    row = next(r for r in cand["candidates"] if r["symbol"] == "PANW")
    assert row["fmp_sector"] is None and row["revenue_ttm"] is None


# --- the user's list is input, not cache ----------------------------------

def test_a_run_that_names_no_peers_never_deletes_the_user_file(tmp_ticker_dir: Path):
    """`peers` is a DEFAULT prefetch kind, so a routine `sra.py prefetch TICKER`
    reaches fetch_peers with no user_peers and must not destroy a list the user
    typed by hand. Staleness is handled by peers-select's timestamp comparison,
    which touches nothing (§13.5)."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, user_peers=["NET", "ZS"],
                peers_provider=lambda t: ["PANW"], now=NOW, **_stub_providers())
    user_path = art(tmp_ticker_dir, "peers_user")
    before = user_path.read_text(encoding="utf-8")
    fetch_peers("CRWD", tmp_ticker_dir, state,
                peers_provider=lambda t: ["PANW"], now=LATER, **_stub_providers())
    assert user_path.read_text(encoding="utf-8") == before


# --- candidates_changed_at is keyed to the SET, not the file --------------

def test_a_re_gather_keeps_the_stamp_when_the_candidate_set_is_unchanged(
        tmp_ticker_dir: Path):
    """The table is rewritten on every gather, and `peers` is a default prefetch
    kind — so the ranking's freshness key must be the candidate SET, not the
    file's mtime, or a routine refresh invalidates a valid ranking (§13.5)."""
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, peers_provider=lambda t: ["PANW"],
                now=NOW, **_stub_providers())
    _, first = read_art(tmp_ticker_dir, "peers_candidates")
    assert first["candidates_changed_at"] == NOW.isoformat()

    fetch_peers("CRWD", tmp_ticker_dir, state, peers_provider=lambda t: ["PANW"],
                now=LATER, **_stub_providers())
    meta, second = read_art(tmp_ticker_dir, "peers_candidates")
    assert meta.computed_at == LATER.isoformat()               # the run is new
    assert second["candidates_changed_at"] == NOW.isoformat()  # the universe is not


def test_a_re_gather_advances_the_stamp_when_a_candidate_appears(
        tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "CRWD")
    fetch_peers("CRWD", tmp_ticker_dir, state, peers_provider=lambda t: ["PANW"],
                now=NOW, **_stub_providers())
    fetch_peers("CRWD", tmp_ticker_dir, state,
                peers_provider=lambda t: ["PANW", "FTNT"], now=LATER,
                **_stub_providers())
    _, data = read_art(tmp_ticker_dir, "peers_candidates")
    assert "FTNT" in {r["symbol"] for r in data["candidates"]}
    assert data["candidates_changed_at"] == LATER.isoformat()


# --- the default providers ------------------------------------------------

def test_fmp_provider_uses_stable_endpoint_and_parses_rows(monkeypatch):
    """The legacy /api/v4/stock_peers surface (payload[0]["peersList"]) now 403s
    for non-legacy keys; /stable/ returns FLAT {symbol, ...} rows."""
    import httpx

    seen: dict = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        return httpx.Response(200, json=[{"symbol": "PANW"}, {"symbol": "ZS"},
                                         {"companyName": "no symbol here"}])

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "get", fake_get)
    assert _fmp_peers("CRWD") == ["PANW", "ZS"]
    assert seen["url"] == FMP_PEERS_URL
    assert seen["params"]["symbol"] == "CRWD"


def test_fmp_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    try:
        _fmp_peers("CRWD")
    except RuntimeError as exc:
        assert "FMP_API_KEY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_fmp_exposure_uses_asset_exposure_endpoint_and_converts_percent(monkeypatch):
    """FMP reports weights in PERCENT; the filters use fractions. Without the
    /100 every fund exceeds MAX_SUBJECT_WEIGHT and source 2 empties silently."""
    import httpx

    seen: dict = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        return httpx.Response(200, json=[
            {"symbol": "VIRS", "weightPercentage": 16.46},
            {"symbol": "SPAM", "weightPercentage": None},
        ])

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "get", fake_get)
    rows = _fmp_exposure("CRWD")
    assert seen["url"] == FMP_EXPOSURE_URL
    assert rows[0] == {"etf_symbol": "VIRS", "weight": 0.1646}
    assert rows[1]["weight"] is None


def test_fmp_fund_holdings_reads_asset_not_symbol(monkeypatch):
    """On /etf/holdings, `symbol` is the FUND; the held security is `asset`."""
    import httpx

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json=[
        {"symbol": "VIRS", "asset": "CRWD", "weightPercentage": 16.0},
        {"symbol": "VIRS", "asset": None, "weightPercentage": 1.0},
    ]))
    rows = _fmp_fund_holdings("VIRS")
    assert rows[0] == {"symbol": "CRWD", "weight": 0.16}
    assert rows[1]["symbol"] is None
