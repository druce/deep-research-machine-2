"""Tests for `sra.py prefetch` (spec §11.1)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sra
from lib.statefile import load_state, record_fetch, save_state

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def stub_registry(monkeypatch):
    """Replace the live registry with stub fetchers, so prefetch is exercised
    without touching a provider."""
    calls: list[str] = []

    def make(kind: str, *, ok: bool = True, err: str | None = None,
             crash: bool = False):
        def fetcher(ticker, ticker_dir, state, **kw):
            calls.append(kind)
            if crash:
                raise RuntimeError("boom")
            if ok:
                record_fetch(state, kind, f"{kind}_stub",
                             datetime.now(timezone.utc), {"policy_days": 1})
            return ok, [], err
        return fetcher

    registry = {"profile": make("profile"), "prices": make("prices"),
                "news": make("news")}
    monkeypatch.setattr(sra, "FETCHERS", registry)
    monkeypatch.setattr(sra, "DEFAULT_KINDS", ["profile", "prices", "news"])
    return {"calls": calls, "registry": registry, "make": make}


def _init(tmp_path: Path) -> Path:
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    return tmp_path / "PANW"


# --- basics ---------------------------------------------------------------

def test_prefetch_runs_the_default_kinds(tmp_path: Path, capsys, stub_registry):
    _init(tmp_path)
    capsys.readouterr()
    assert sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["fetched"]) == {"profile", "prices", "news"}
    assert out["errors"] == {}


def test_prefetch_records_state(tmp_path: Path, capsys, stub_registry):
    d = _init(tmp_path)
    sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)])
    assert load_state(d)["data"]["prices"]["current_ids"] == ["prices_stub"]


def test_kinds_flag_restricts_the_run(tmp_path: Path, capsys, stub_registry):
    _init(tmp_path)
    capsys.readouterr()
    sra.main(["prefetch", "PANW", "--kinds", "prices", "--data-root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["fetched"] == ["prices"]
    assert stub_registry["calls"] == ["prices"]


def test_kinds_flag_tolerates_whitespace_and_duplicates(tmp_path: Path, capsys,
                                                        stub_registry):
    """"prices, news" is two kinds, not one named " news" that would fail the
    unknown-kind check."""
    _init(tmp_path)
    capsys.readouterr()
    sra.main(["prefetch", "PANW", "--kinds", "prices, news,prices,",
              "--data-root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["fetched"] == ["prices", "news"]


def test_an_unknown_kind_exits_1_without_running_anything(tmp_path: Path,
                                                          stub_registry):
    _init(tmp_path)
    assert sra.main(["prefetch", "PANW", "--kinds", "prices,nope",
                     "--data-root", str(tmp_path)]) == 1
    assert stub_registry["calls"] == []


def test_prefetch_exits_1_when_uninitialized(tmp_path: Path, stub_registry):
    assert sra.main(["prefetch", "MSFT", "--data-root", str(tmp_path)]) == 1


def test_prefetch_rejects_a_traversal_ticker(tmp_path: Path, stub_registry):
    assert sra.main(["prefetch", "../evil", "--data-root", str(tmp_path)]) == 1


def test_fetchers_see_the_upper_cased_ticker(tmp_path: Path, monkeypatch):
    seen: list[str] = []

    def fetcher(ticker, ticker_dir, state, **kw):
        seen.append(ticker)
        return True, [], None

    monkeypatch.setattr(sra, "FETCHERS", {"prices": fetcher})
    monkeypatch.setattr(sra, "DEFAULT_KINDS", ["prices"])
    sra.main(["init", "panw", "--data-root", str(tmp_path)])
    sra.main(["prefetch", "panw", "--data-root", str(tmp_path)])
    assert seen == ["PANW"]


# --- failure reporting ----------------------------------------------------

def test_a_failing_fetcher_exits_2(tmp_path: Path, capsys, monkeypatch,
                                   stub_registry):
    _init(tmp_path)
    monkeypatch.setitem(sra.FETCHERS, "prices",
                        stub_registry["make"]("prices", ok=False, err="provider said no"))
    capsys.readouterr()
    assert sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["errors"]["prices"] == "provider said no"


def test_a_crashing_fetcher_exits_2_and_others_still_run(tmp_path: Path, capsys,
                                                         monkeypatch, stub_registry):
    _init(tmp_path)
    monkeypatch.setitem(sra.FETCHERS, "prices",
                        stub_registry["make"]("prices", crash=True))
    capsys.readouterr()
    assert sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert "prices crashed" in out["errors"]["prices"]
    assert set(out["fetched"]) == {"profile", "news"}


def test_a_warning_does_not_fail_the_run(tmp_path: Path, capsys, monkeypatch,
                                         stub_registry):
    _init(tmp_path)
    monkeypatch.setitem(sra.FETCHERS, "prices",
                        stub_registry["make"]("prices", err="FMP top-up failed"))
    capsys.readouterr()
    assert sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["warnings"]["prices"] == "FMP top-up failed"
    assert "prices" in out["fetched"]


# --- crash consistency (§7.1, §24) ---------------------------------------

def test_a_crash_leaves_earlier_work_recorded(tmp_path: Path, monkeypatch,
                                              stub_registry):
    """§7.1: state is committed after each fetcher, so a later crash cannot
    discard an earlier success."""
    d = _init(tmp_path)
    monkeypatch.setitem(sra.FETCHERS, "news", stub_registry["make"]("news", crash=True))
    sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)])
    assert "profile" in load_state(d)["data"]


def test_rerunning_after_a_crash_neither_crashes_nor_double_counts(
        tmp_path: Path, capsys, monkeypatch, stub_registry):
    """Every phase is idempotent and rerunning is the sanctioned recovery
    (§7.1) — the rerun must not trip over what the first run committed."""
    d = _init(tmp_path)
    monkeypatch.setitem(sra.FETCHERS, "news", stub_registry["make"]("news", crash=True))
    sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)])

    monkeypatch.setitem(sra.FETCHERS, "news", stub_registry["make"]("news"))
    capsys.readouterr()
    assert sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)]) == 0
    assert set(json.loads(capsys.readouterr().out)["fetched"]) == {
        "profile", "prices", "news"}
    assert load_state(d)["data"]["news"]["current_ids"] == ["news_stub"]


# --- --stale-only ---------------------------------------------------------

def test_stale_only_skips_fresh_kinds(tmp_path: Path, capsys, stub_registry):
    d = _init(tmp_path)
    state = load_state(d)
    record_fetch(state, "prices", "prices_stub", datetime.now(timezone.utc),
                 {"policy_days": 1})
    save_state(d, state)
    (d / "structured" / "prices_stub.json").write_text("{}", encoding="utf-8")
    capsys.readouterr()

    sra.main(["prefetch", "PANW", "--stale-only", "--data-root", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert "prices" not in out["fetched"]
    assert "prices" in out["skipped"]
    assert set(out["fetched"]) == {"profile", "news"}


def test_stale_only_refetches_an_expired_kind(tmp_path: Path, capsys, stub_registry):
    d = _init(tmp_path)
    state = load_state(d)
    record_fetch(state, "prices", "prices_stub",
                 datetime.now(timezone.utc) - timedelta(days=5), {"policy_days": 1})
    save_state(d, state)
    (d / "structured" / "prices_stub.json").write_text("{}", encoding="utf-8")
    capsys.readouterr()

    sra.main(["prefetch", "PANW", "--stale-only", "--data-root", str(tmp_path)])
    assert "prices" in json.loads(capsys.readouterr().out)["fetched"]


def test_stale_only_runs_kinds_never_fetched(tmp_path: Path, capsys, stub_registry):
    _init(tmp_path)
    capsys.readouterr()
    sra.main(["prefetch", "PANW", "--stale-only", "--data-root", str(tmp_path)])
    assert set(json.loads(capsys.readouterr().out)["fetched"]) == {
        "profile", "prices", "news"}


def test_stale_only_treats_a_gathered_but_unselected_peers_as_fresh(
        tmp_path: Path, capsys, monkeypatch, stub_registry):
    """KIND_STAGES: the gather records `peers_candidates` while `peers-select`
    records `peers`. Without stage aliasing a gathered-but-unselected ticker
    looks never-fetched and re-runs the whole 40-70-call gather every sweep."""
    d = _init(tmp_path)
    monkeypatch.setitem(sra.FETCHERS, "peers", stub_registry["make"]("peers"))
    monkeypatch.setattr(sra, "DEFAULT_KINDS", ["peers"])
    state = load_state(d)
    record_fetch(state, "peers_candidates", "peers_candidates",
                 datetime.now(timezone.utc), {"policy_days": 90})
    save_state(d, state)
    (d / "derived" / "peers").mkdir(parents=True, exist_ok=True)
    (d / "derived" / "peers" / "peers_candidates.json").write_text("{}", encoding="utf-8")
    capsys.readouterr()

    sra.main(["prefetch", "PANW", "--stale-only", "--data-root", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert out["fetched"] == []
    assert out["skipped"] == ["peers"]


# --- peers kwarg and locking ---------------------------------------------

def test_user_peers_reach_only_the_peers_fetcher(tmp_path: Path, monkeypatch):
    seen: dict = {}

    def peers(ticker, ticker_dir, state, *, user_peers=None, **kw):
        seen["user_peers"] = user_peers
        return True, [], None

    def prices(ticker, ticker_dir, state, **kw):
        seen["prices_kwargs"] = dict(kw)
        return True, [], None

    monkeypatch.setattr(sra, "FETCHERS", {"prices": prices, "peers": peers})
    monkeypatch.setattr(sra, "DEFAULT_KINDS", ["prices", "peers"])
    sra.main(["init", "PANW", "--data-root", str(tmp_path)])
    sra.main(["prefetch", "PANW", "--peers", "CRWD,FTNT", "--data-root", str(tmp_path)])
    assert seen["user_peers"] == ["CRWD", "FTNT"]
    assert "user_peers" not in seen["prices_kwargs"]


def test_prefetch_takes_the_lock(tmp_path: Path, stub_registry):
    d = _init(tmp_path)
    (d / ".lock").write_text(json.dumps({
        "pid": 4242, "command": "manifest",
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    assert sra.main(["prefetch", "PANW", "--data-root", str(tmp_path)]) == 1
    assert stub_registry["calls"] == []
