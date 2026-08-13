"""The OpenBB-backed ownership fetcher (spec §11.1).

Ownership, insider activity and short interest were the one gap in the
deterministic gather, and the topic briefs were paying agents to approximate
them from news articles. This fetcher closes it.

OpenBB is not a dependency of this project — it runs out of process — so the
subprocess is injected here and no test needs OpenBB, a network, or a provider
key. What has to hold:

- absence degrades (a warning and no artifact), because ownership is context and
  §11.1 makes only profile/prices/financials fatal;
- a partial answer is kept, since two datasets out of three is still useful and
  OpenBB's own providers fail individually all the time;
- what failed is recorded IN the artifact, so a reader can tell "no insider
  selling" from "the insider feed was down";
- credentials never reach argv.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lib.fetchers.ownership import (
    ARTIFACT_ID, OWNERSHIP_POLICY_DAYS, fetch_ownership, openbb_python,
    run_openbb)
from lib.provenance import read_structured
from lib.statefile import init_state

FAKE_PYTHON = "/usr/bin/python3"       # exists everywhere these tests run


def runner_returning(payload: dict, *, returncode: int = 0, stderr: str = ""):
    """A `subprocess.run` stand-in that prints `payload` as the runner would."""
    calls: list[dict] = []

    def _run(argv, **kwargs):
        calls.append({"argv": argv, "env": kwargs.get("env") or {}})
        return subprocess.CompletedProcess(
            argv, returncode, stdout=json.dumps(payload) + "\n", stderr=stderr)

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


def full_payload() -> dict:
    return {
        "data": {
            "institutional": [{"symbol": "TOST", "investors_holding": 812}],
            "insider_trading": [
                {"symbol": "TOST", "transaction_date": "2026-08-08",
                 "owner_name": "Narang Aman", "securities_transacted": 138000},
            ],
            "short_interest": [
                {"settlement_date": "2026-07-31", "current_short_position": 21_400_000},
            ],
        },
        "providers": {"institutional": "fmp", "insider_trading": "fmp",
                      "short_interest": "finra"},
        "errors": {},
    }


@pytest.fixture
def ticker_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data" / "TOST"
    for sub in ("structured", "sources", "derived"):
        (d / sub).mkdir(parents=True)
    return d


# --- interpreter resolution ------------------------------------------------

def test_no_interpreter_configured_degrades(monkeypatch, ticker_dir: Path):
    """§11.1: ownership is context, never minimum-viable input."""
    monkeypatch.delenv("OPENBB_PYTHON", raising=False)
    monkeypatch.setattr("lib.fetchers.ownership.openbb_python", lambda: None)

    ok, paths, err = fetch_ownership("TOST", ticker_dir, init_state(ticker_dir, "TOST"))

    assert (ok, paths) == (False, [])
    assert "openbb_unavailable" in err
    assert "OPENBB_PYTHON" in err


def test_a_configured_interpreter_that_does_not_exist_degrades(monkeypatch):
    monkeypatch.setenv("OPENBB_PYTHON", "/nonexistent/bin/python")
    assert openbb_python() is None


def test_the_env_override_wins(monkeypatch):
    monkeypatch.setenv("OPENBB_PYTHON", FAKE_PYTHON)
    assert openbb_python() == FAKE_PYTHON


def test_a_blank_override_is_ignored(monkeypatch):
    """An empty env var must not be read as "an interpreter at path ''"."""
    monkeypatch.setenv("OPENBB_PYTHON", "   ")
    monkeypatch.setattr("lib.fetchers.ownership.sys.executable", FAKE_PYTHON)
    assert openbb_python() in (FAKE_PYTHON, None)   # falls through to the import check


# --- the subprocess boundary ------------------------------------------------

def test_credentials_never_reach_argv(monkeypatch):
    """argv is world-readable in `ps`; keys go in the environment only."""
    monkeypatch.setenv("FMP_API_KEY", "super-secret-key")
    run = runner_returning(full_payload())

    run_openbb("TOST", interpreter=FAKE_PYTHON, runner=run)

    argv = run.calls[0]["argv"]
    assert "super-secret-key" not in " ".join(argv)
    assert run.calls[0]["env"]["FMP_API_KEY"] == "super-secret-key"
    assert run.calls[0]["env"]["OPENBB_SYMBOL"] == "TOST"


def test_a_crashing_runner_is_an_error_not_an_exception():
    run = runner_returning({}, returncode=1, stderr="ImportError: no openbb\n")

    payload, err = run_openbb("TOST", interpreter=FAKE_PYTHON, runner=run)

    assert payload is None
    assert "openbb_error" in err


def test_a_timeout_is_reported_not_raised():
    def _run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 180)

    payload, err = run_openbb("TOST", interpreter=FAKE_PYTHON, runner=_run)

    assert payload is None
    assert "openbb_timeout" in err


def test_unparseable_stdout_is_reported():
    def _run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="not json\n", stderr="")

    payload, err = run_openbb("TOST", interpreter=FAKE_PYTHON, runner=_run)

    assert payload is None
    assert "no parseable JSON" in err


def test_a_fatal_import_inside_the_runner_degrades():
    run = runner_returning({"fatal": "openbb import failed: ImportError: nope"})

    payload, err = run_openbb("TOST", interpreter=FAKE_PYTHON, runner=run)

    assert payload is None
    assert "openbb_unavailable" in err


def test_only_the_last_stdout_line_is_parsed():
    """OpenBB's providers emit warnings on stdout; the payload is the last line."""
    def _run(argv, **kwargs):
        noisy = "FutureWarning: whatever\nloading providers...\n"
        return subprocess.CompletedProcess(
            argv, 0, stdout=noisy + json.dumps(full_payload()) + "\n", stderr="")

    payload, err = run_openbb("TOST", interpreter=FAKE_PYTHON, runner=_run)

    assert err is None
    assert set(payload["data"]) == {"institutional", "insider_trading",
                                    "short_interest"}


# --- the artifact -----------------------------------------------------------

def test_a_full_fetch_writes_a_bronze_artifact(ticker_dir: Path):
    state = init_state(ticker_dir, "TOST")

    ok, paths, err = fetch_ownership(
        "TOST", ticker_dir, state,
        interpreter=FAKE_PYTHON, runner=runner_returning(full_payload()))

    assert (ok, err) == (True, None)
    assert paths[0].name == f"{ARTIFACT_ID}.json"

    meta, data = read_structured(paths[0])
    assert meta.producer == "fetch"          # bronze, therefore citable
    assert meta.source == "OpenBB"
    assert meta.ticker == "TOST"
    assert set(data["datasets"]) == {"institutional", "insider_trading",
                                     "short_interest"}
    assert data["providers"]["short_interest"] == "finra"


def test_the_fetch_is_recorded_in_state(ticker_dir: Path):
    state = init_state(ticker_dir, "TOST")

    fetch_ownership("TOST", ticker_dir, state, interpreter=FAKE_PYTHON,
                    runner=runner_returning(full_payload()))

    entry = state["data"]["ownership"]
    assert entry["current_ids"] == [ARTIFACT_ID]
    assert entry["policy_days"] == OWNERSHIP_POLICY_DAYS


def test_a_partial_answer_is_kept_with_a_warning(ticker_dir: Path):
    """Two datasets out of three is useful; failing the kind would discard them.
    OpenBB's `major_holders` raises on its own data model and stockgrid returns
    HTML, so partial is the normal case."""
    payload = full_payload()
    payload["data"].pop("short_interest")
    payload["errors"] = {"short_interest": "OpenBBError: provider unavailable"}

    ok, paths, err = fetch_ownership(
        "TOST", ticker_dir, init_state(ticker_dir, "TOST"),
        interpreter=FAKE_PYTHON, runner=runner_returning(payload))

    assert ok is True
    assert paths
    assert "openbb_partial" in err
    assert "short_interest" in err


def test_what_failed_is_recorded_in_the_artifact(ticker_dir: Path):
    """A reader must be able to tell "no insider selling" from "the feed was
    down" — six months later, with only the artifact to go on."""
    payload = full_payload()
    payload["data"].pop("insider_trading")
    payload["errors"] = {"insider_trading": "HTTPError: 402"}

    _ok, paths, _err = fetch_ownership(
        "TOST", ticker_dir, init_state(ticker_dir, "TOST"),
        interpreter=FAKE_PYTHON, runner=runner_returning(payload))

    _meta, data = read_structured(paths[0])
    assert "insider_trading" in data["unavailable"]
    assert "402" in data["unavailable"]["insider_trading"]


def test_every_dataset_failing_is_a_failed_kind(ticker_dir: Path):
    payload = {"data": {}, "providers": {},
               "errors": {"institutional": "boom", "insider_trading": "boom",
                          "short_interest": "boom"}}

    ok, paths, err = fetch_ownership(
        "TOST", ticker_dir, init_state(ticker_dir, "TOST"),
        interpreter=FAKE_PYTHON, runner=runner_returning(payload))

    assert (ok, paths) == (False, [])
    assert "openbb_empty" in err


def test_the_artifact_passes_validate(ticker_dir: Path):
    from lib.validate import validate

    fetch_ownership("TOST", ticker_dir, init_state(ticker_dir, "TOST"),
                    interpreter=FAKE_PYTHON, runner=runner_returning(full_payload()))

    errors = [f for f in validate(ticker_dir, ticker_dir.parent)
              if f.severity == "error"]
    assert errors == []


def test_rows_are_capped_newest_first(ticker_dir: Path):
    """Regression. Providers disagree about row order — FMP returns Form 4s
    newest-first, FINRA returns short interest OLDEST-first — so capping a raw
    list kept FINRA's oldest rows and reported 2021 short interest as current
    on a 2026 build. The runner sorts before it caps.

    The sort lives in the subprocess, so this asserts the contract the fetcher
    depends on: whatever arrives is stored in the order given, newest first.
    """
    payload = full_payload()
    payload["data"]["short_interest"] = [
        {"settlement_date": "2026-07-15", "current_short_position": 40_780_362},
        {"settlement_date": "2026-06-30", "current_short_position": 39_100_000},
        {"settlement_date": "2025-06-30", "current_short_position": 21_400_000},
    ]

    _ok, paths, _err = fetch_ownership(
        "TOST", ticker_dir, init_state(ticker_dir, "TOST"),
        interpreter=FAKE_PYTHON, runner=runner_returning(payload))

    _meta, data = read_structured(paths[0])
    dates = [r["settlement_date"] for r in data["datasets"]["short_interest"]]
    assert dates == sorted(dates, reverse=True), "newest settlement must lead"


def test_the_runner_sorts_newest_first_before_capping():
    """The sort itself, exercised directly out of the runner source."""
    from lib.fetchers.ownership import _RUNNER

    namespace: dict = {}
    body = _RUNNER.split("out = {")[0]          # the helpers, without the main flow
    exec(compile(body, "<runner>", "exec"), namespace)   # noqa: S102 — our own source

    rows = [{"settlement_date": "2025-06-30"}, {"settlement_date": "2026-07-15"},
            {"settlement_date": "2026-06-30"}]
    out = namespace["_newest_first"](rows)

    assert [r["settlement_date"] for r in out] == [
        "2026-07-15", "2026-06-30", "2025-06-30"]


def test_rows_without_a_date_field_are_not_dropped():
    from lib.fetchers.ownership import _RUNNER

    namespace: dict = {}
    exec(compile(_RUNNER.split("out = {")[0], "<runner>", "exec"), namespace)  # noqa: S102

    rows = [{"investors_holding": 812}, {"investors_holding": 799}]
    assert len(namespace["_newest_first"](rows)) == 2


# --- registry wiring --------------------------------------------------------

def test_ownership_is_a_registered_default_kind():
    from lib.fetchers.registry import DEFAULT_KINDS, FATAL_KINDS, FETCHERS

    assert "ownership" in FETCHERS
    assert "ownership" in DEFAULT_KINDS
    assert "ownership" not in FATAL_KINDS   # context, not minimum-viable input
