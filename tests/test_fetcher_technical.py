"""Tests for the technical-indicators fetcher (spec §6.2, §11.1).

Its sole input is the stored prices artifact, so the whole computation is
offline and reproducible — which is what makes `derived_from: ["prices_yahoo"]`
literally true rather than decorative.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.fetchers.technical import DEPENDS_ON, fetch_technical
from lib.provenance import read_structured
from lib.statefile import init_state
from lib.validate import validate

pytest.importorskip("talib", reason="ta-lib C library not installed")

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _write_prices(ticker_dir: Path, n: int = 260, *, daily: dict | None = None) -> None:
    """Plant a prices artifact directly: technical reads the file, not a provider."""
    if daily is None:
        dates = [(datetime(2025, 1, 1) + __import__("datetime").timedelta(days=i)
                  ).date().isoformat() for i in range(n)]
        daily = {
            "dates": dates,
            "open": [100.0 + i * 0.1 for i in range(n)],
            "high": [101.0 + i * 0.1 for i in range(n)],
            "low": [99.0 + i * 0.1 for i in range(n)],
            "close": [100.0 + i * 0.1 for i in range(n)],
            "volume": [1_000_000 + i for i in range(n)],
        }
    payload = {
        "_meta": {"id": "prices_yahoo", "ticker": "PANW", "producer": "fetch",
                  "title": "PANW daily OHLCV prices", "source": "Yahoo Finance",
                  "url": "https://finance.yahoo.com/quote/PANW/history",
                  "provider_tool": "yfinance.download",
                  "fetch_cmd": "uv run python sra.py prefetch PANW --kinds prices",
                  "fetched_at": NOW.isoformat(),
                  "as_of": daily["dates"][-1], "derived_from": []},
        "data": {"daily": daily, "benchmark": None},
    }
    (ticker_dir / "structured" / "prices_yahoo.json").write_text(
        json.dumps(payload), encoding="utf-8")


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- contract -------------------------------------------------------------

def test_depends_on_prices():
    """§11.1's wave edge: technical recomputes from the artifact prices writes."""
    assert DEPENDS_ON == ("prices",)


def test_writes_a_compute_shaped_artifact(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir)
    ok, paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert ok and err is None and len(paths) == 1

    meta, data = read_structured(
        tmp_ticker_dir / "structured" / "technical_indicators_computed.json")
    assert meta.producer == "compute"
    assert meta.derived_from == ["prices_yahoo"]
    assert meta.computed_at == NOW.isoformat()
    assert meta.url is None  # §6.2: compute forbids url
    assert meta.fetch_cmd
    assert data["indicators"]["sma_200"] is not None


def test_as_of_is_the_last_bar(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir)
    fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    meta, data = read_structured(
        tmp_ticker_dir / "structured" / "technical_indicators_computed.json")
    assert meta.as_of == data["date"]


def test_writes_no_chart(tmp_ticker_dir: Path):
    """Chart rendering moved to lib/charts/ (§16). Keeping plotly out of a
    bronze fetcher means a missing headless-Chrome runtime cannot fail evidence
    gathering."""
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir)
    _ok, paths, _err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert all(p.suffix == ".json" for p in paths)
    assert [p for p in (tmp_ticker_dir / "charts").rglob("*") if p.is_file()] == []


def test_output_passes_validation(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir)
    fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert _errors(tmp_ticker_dir) == []


def test_records_state(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir)
    fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert state["data"]["technical"]["current_ids"] == [
        "technical_indicators_computed"]


# --- malformed input degrades to the error contract ----------------------

def test_missing_prices_artifact_fails_with_guidance(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    ok, _paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert not ok and "prices" in err


def test_unreadable_prices_artifact_fails_cleanly(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    (tmp_ticker_dir / "structured" / "prices_yahoo.json").write_text(
        '{"_meta": {}}', encoding="utf-8")
    ok, _paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert not ok and err


def test_missing_series_fails_cleanly(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir, daily={"dates": ["2026-07-30"], "close": [1.0]})
    ok, _paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert not ok and "missing" in err


def test_non_numeric_series_fails_cleanly(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir, daily={
        "dates": ["2026-07-29", "2026-07-30"], "open": [1.0, 2.0],
        "high": [1.0, 2.0], "low": [1.0, 2.0], "close": ["not", "numeric"],
        "volume": [1, 2]})
    ok, _paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert not ok and "not numeric" in err


def test_ragged_series_fails_cleanly(tmp_ticker_dir: Path):
    """talib combines high/low/close for ATR and would raise on ragged input."""
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir, daily={
        "dates": ["2026-07-29", "2026-07-30"], "open": [1.0, 2.0],
        "high": [1.0], "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [1, 2]})
    ok, _paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert not ok and "mismatched" in err


def test_too_little_history_fails_cleanly(tmp_ticker_dir: Path):
    state = init_state(tmp_ticker_dir, "PANW")
    _write_prices(tmp_ticker_dir, daily={
        "dates": ["2026-07-30"], "open": [1.0], "high": [1.0], "low": [1.0],
        "close": [1.0], "volume": [1]})
    ok, _paths, err = fetch_technical("PANW", tmp_ticker_dir, state, now=NOW)
    assert not ok and "not enough price history" in err
