"""Registry contract: dependency waves, concurrency safety, per-fetcher commits.

Spec §11.1: fetchers run as in-process futures, only the main thread mutates
shared state, and state is committed after each fetcher returns — whether it
succeeded, warned, failed, or crashed (§7.1).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.fetchers.registry import (
    DEFAULT_KINDS,
    FATAL_KINDS,
    KIND_ORDER,
    KIND_STAGES,
    STAGE_OF,
    CycleError,
    run_prefetch,
    waves,
)
from lib.statefile import init_state, load_state, record_fetch, save_state

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _ok(*, records: str | None = None, warn: str | None = None):
    """A stub fetcher that optionally records a fetch into the state it is given."""
    def fetcher(ticker, ticker_dir, state, **kw):
        if records:
            record_fetch(state, records, f"{records}_stub", NOW, {"policy_days": 1})
        return True, [], warn
    return fetcher


def _fails(msg: str = "provider said no"):
    def fetcher(ticker, ticker_dir, state, **kw):
        return False, [], msg
    return fetcher


def _crashes():
    def fetcher(ticker, ticker_dir, state, **kw):
        raise RuntimeError("boom")
    return fetcher


# --- spec-pinned registry data -------------------------------------------

def test_kind_order_matches_the_spec():
    assert KIND_ORDER == (
        "profile", "prices", "technical", "financials", "estimates", "targets",
        "calendar", "peers", "filings", "transcript", "wikipedia", "news",
        "perplexity",
    )


def test_perplexity_is_opt_in():
    """§11.1: the twelve default kinds; perplexity is a supplement."""
    assert DEFAULT_KINDS == [k for k in KIND_ORDER if k != "perplexity"]
    assert len(DEFAULT_KINDS) == 12


def test_fatal_kinds_are_the_minimum_viable_input():
    """§11.1: failure of any of these stops /sra-build; the rest degrade."""
    assert FATAL_KINDS == ("profile", "prices", "financials")


def test_peers_is_the_only_staged_kind():
    """The gather writes `peers_candidates` and `peers-select` writes `peers`,
    so prefetch must know both keys — otherwise a ticker that was gathered but
    not yet selected looks never-fetched and re-runs the whole gather on every
    --stale-only sweep, forever."""
    assert KIND_STAGES == {"peers": ("peers", "peers_candidates")}
    assert STAGE_OF["peers_candidates"] == "peers"


# --- dependency waves (§11.1) --------------------------------------------

def test_prices_precedes_technical():
    """technical recomputes from the artifact prices writes in the same run."""
    order = [k for wave in waves(["technical", "prices"],
                                 {"technical": ("prices",)}) for k in wave]
    assert order.index("prices") < order.index("technical")


def test_profile_precedes_wikipedia():
    """wikipedia searches on state["company_name"], which profile populates."""
    order = [k for wave in waves(["wikipedia", "profile"],
                                 {"wikipedia": ("profile",)}) for k in wave]
    assert order.index("profile") < order.index("wikipedia")


def test_independent_kinds_share_one_wave(tmp_path: Path):
    assert waves(["profile", "prices", "news"], {}) == [["profile", "prices", "news"]]


def test_the_spec_wave_split(tmp_path: Path):
    """§11.1's two waves: everything, then technical and wikipedia."""
    deps = {"technical": ("prices",), "wikipedia": ("profile",)}
    result = waves(list(DEFAULT_KINDS), deps)
    assert len(result) == 2
    assert set(result[1]) == {"technical", "wikipedia"}


def test_waves_preserve_registry_order_within_a_wave():
    """Deterministic scheduling: same request, same grouping, every run."""
    assert waves(["news", "profile", "prices"], {}) == [["news", "profile", "prices"]]


def test_a_dependency_outside_the_request_is_not_pulled_in():
    """`prefetch T --kinds technical` must run, working off the prices artifact
    already on disk — a --kinds request is explicit, not a hint."""
    assert waves(["technical"], {"technical": ("prices",)}) == [["technical"]]


def test_a_dependency_cycle_is_rejected():
    with pytest.raises(CycleError):
        waves(["a", "b"], {"a": ("b",), "b": ("a",)})


# --- running: state commits and isolation --------------------------------

def test_state_is_committed_after_each_fetcher(tmp_ticker_dir: Path):
    """§7.1: saving after each fetcher keeps a crash from leaving completed
    artifacts unrecorded in state."""
    state = init_state(tmp_ticker_dir, "PANW")
    commits: list[int] = []

    def watcher(_state):
        commits.append(len(load_state(tmp_ticker_dir)["data"]))

    run_prefetch("PANW", tmp_ticker_dir, state,
                 ["profile", "prices"],
                 {"profile": _ok(records="profile"), "prices": _ok(records="prices")},
                 {}, on_commit=watcher)
    assert commits == [1, 2]


def test_a_fetcher_never_receives_the_shared_state_object(tmp_ticker_dir: Path):
    """§11.1: only the main thread mutates shared state. Fetchers run in
    worker threads, so each gets a private copy and the main thread merges
    the delta in — a fetcher writing straight into the shared dict from a
    worker is the race this design exists to prevent."""
    state = init_state(tmp_ticker_dir, "PANW")
    seen: list[int] = []

    def fetcher(ticker, ticker_dir, s, **kw):
        seen.append(id(s))
        return True, [], None

    run_prefetch("PANW", tmp_ticker_dir, state, ["profile"], {"profile": fetcher}, {})
    assert seen and seen[0] != id(state)


def test_a_fetchers_state_writes_reach_the_shared_state(tmp_ticker_dir: Path):
    """The private copy is an isolation mechanism, not a black hole: whatever
    the fetcher recorded has to land in the saved state."""
    state = init_state(tmp_ticker_dir, "PANW")
    run_prefetch("PANW", tmp_ticker_dir, state, ["prices"],
                 {"prices": _ok(records="prices")}, {})
    assert state["data"]["prices"]["current_ids"] == ["prices_stub"]
    assert load_state(tmp_ticker_dir)["data"]["prices"]["current_ids"] == ["prices_stub"]


def test_scalar_state_updates_are_merged(tmp_ticker_dir: Path):
    """profile sets company_name, which wikipedia reads in the next wave."""
    state = init_state(tmp_ticker_dir, "PANW")

    def profile(ticker, ticker_dir, s, **kw):
        s["company_name"] = "Palo Alto Networks, Inc."
        return True, [], None

    seen: list[str | None] = []

    def wikipedia(ticker, ticker_dir, s, **kw):
        seen.append(s["company_name"])
        return True, [], None

    run_prefetch("PANW", tmp_ticker_dir, state, ["profile", "wikipedia"],
                 {"profile": profile, "wikipedia": wikipedia},
                 {"wikipedia": ("profile",)})
    assert state["company_name"] == "Palo Alto Networks, Inc."
    assert seen == ["Palo Alto Networks, Inc."]


def test_concurrent_fetchers_in_one_wave_all_land(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    kinds = ["profile", "prices", "news", "targets", "estimates"]
    run_prefetch("PANW", tmp_ticker_dir, state, kinds,
                 {k: _ok(records=k) for k in kinds}, {})
    assert set(state["data"]) == set(kinds)


def test_fetchers_in_a_wave_run_concurrently(tmp_ticker_dir: Path):
    """Not merely interleaved — the point of the wave is wall-clock overlap."""
    state = init_state(tmp_ticker_dir, "PANW")
    barrier = threading.Barrier(3, timeout=10)

    def fetcher(ticker, ticker_dir, s, **kw):
        barrier.wait()  # deadlocks (and fails) unless all three run at once
        return True, [], None

    result = run_prefetch("PANW", tmp_ticker_dir, state,
                          ["profile", "prices", "news"],
                          {k: fetcher for k in ("profile", "prices", "news")}, {})
    assert set(result["fetched"]) == {"profile", "prices", "news"}


# --- failure handling ----------------------------------------------------

def test_a_failing_fetcher_is_reported_not_raised(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    result = run_prefetch("PANW", tmp_ticker_dir, state, ["prices"],
                          {"prices": _fails()}, {})
    assert result["errors"] == {"prices": "provider said no"}
    assert result["fetched"] == []


def test_a_crashing_fetcher_becomes_an_error_entry(tmp_ticker_dir: Path):
    """§11.1: uncaught exceptions become errors[kind] = "<kind> crashed: ..."."""
    state = init_state(tmp_ticker_dir, "PANW")
    result = run_prefetch("PANW", tmp_ticker_dir, state, ["prices"],
                          {"prices": _crashes()}, {})
    assert "prices crashed" in result["errors"]["prices"]
    assert "boom" in result["errors"]["prices"]


def test_a_crash_does_not_stop_later_fetchers(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    result = run_prefetch("PANW", tmp_ticker_dir, state, ["prices", "technical"],
                          {"prices": _crashes(), "technical": _ok(records="technical")},
                          {"technical": ("prices",)})
    assert "prices" in result["errors"]
    assert result["fetched"] == ["technical"]


def test_a_crash_does_not_discard_prior_state_commits(tmp_ticker_dir: Path):
    """§7.1: "one fetcher failure does not discard prior successful work"."""
    state = init_state(tmp_ticker_dir, "PANW")
    run_prefetch("PANW", tmp_ticker_dir, state, ["profile", "technical"],
                 {"profile": _ok(records="profile"), "technical": _crashes()},
                 {"technical": ("profile",)})
    assert "profile" in load_state(tmp_ticker_dir)["data"]


def test_success_with_a_warning_counts_as_fetched(tmp_ticker_dir: Path):
    """(True, paths, err) is success with a warning (§11.1) — it must not be
    reported as a failure, or a degradable partial result would fail a build."""
    state = init_state(tmp_ticker_dir, "PANW")
    result = run_prefetch("PANW", tmp_ticker_dir, state, ["peers"],
                          {"peers": _ok(records="peers", warn="FMP top-up failed")}, {})
    assert result["fetched"] == ["peers"]
    assert result["warnings"] == {"peers": "FMP top-up failed"}
    assert result["errors"] == {}


def test_state_is_committed_even_when_a_fetcher_crashes(tmp_ticker_dir: Path):
    """§7.1 commits after each fetcher returns "whether it succeeds, succeeds
    with warnings, fails cleanly, or raises"."""
    state = init_state(tmp_ticker_dir, "PANW")
    state["company_name"] = "set before the run"
    save_state(tmp_ticker_dir, state)
    commits: list[int] = []
    run_prefetch("PANW", tmp_ticker_dir, state, ["prices"], {"prices": _crashes()}, {},
                 on_commit=lambda s: commits.append(1))
    assert commits == [1]


def test_an_unknown_kind_is_rejected_before_anything_runs(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    with pytest.raises(KeyError):
        run_prefetch("PANW", tmp_ticker_dir, state, ["nope"], {"prices": _ok()}, {})


# --- extra kwargs ---------------------------------------------------------

def test_extra_kwargs_reach_only_the_named_kind(tmp_ticker_dir: Path):
    """peers is the one fetcher taking user_peers; passing it to every fetcher
    would break the uniform signature."""
    state = init_state(tmp_ticker_dir, "PANW")
    seen: dict[str, dict] = {}

    def fetcher(ticker, ticker_dir, s, **kw):
        seen[ticker] = dict(kw)
        return True, [], None

    def peers(ticker, ticker_dir, s, *, user_peers=None, **kw):
        seen["peers_arg"] = {"user_peers": user_peers}
        return True, [], None

    run_prefetch("PANW", tmp_ticker_dir, state, ["prices", "peers"],
                 {"prices": fetcher, "peers": peers}, {},
                 extra_kwargs={"peers": {"user_peers": ["CRWD"]}})
    assert seen["peers_arg"] == {"user_peers": ["CRWD"]}
    assert "user_peers" not in seen.get("PANW", {})
