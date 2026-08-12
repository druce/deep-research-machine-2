"""prefetch-peers builds metric bronze for the selected comparables."""

from __future__ import annotations

import json
from pathlib import Path

import sra


def _subject(tmp_path: Path, peers: list[str] | None) -> Path:
    data_root = tmp_path / "data"
    d = data_root / "SUBJ"
    (d / "derived" / "peers").mkdir(parents=True)
    (d / "structured").mkdir(parents=True)
    sra.init_state(d, "SUBJ")
    if peers is not None:
        (d / "derived" / "peers" / "peers_selected.json").write_text(json.dumps({
            "_meta": {"id": "peers_selected", "ticker": "SUBJ", "producer": "model"},
            "data": {"peers": [{"symbol": s, "is_subject": False} for s in peers]},
        }), encoding="utf-8")
    return data_root


def _args(data_root: Path, *extra: str):
    return sra.build_parser().parse_args(
        ["prefetch-peers", "SUBJ", "--data-root", str(data_root), *extra])


def test_missing_selection_exits_1(tmp_path: Path, capsys) -> None:
    args = _args(_subject(tmp_path, None))

    assert args.fn(args) == 1
    assert "peers-select" in capsys.readouterr().err


def test_symbols_exclude_the_subject(tmp_path: Path) -> None:
    data_root = _subject(tmp_path, ["BA", "LMT"])
    selection = data_root / "SUBJ" / "derived" / "peers" / "peers_selected.json"
    payload = json.loads(selection.read_text(encoding="utf-8"))
    payload["data"]["peers"].append({"symbol": "SUBJ", "is_subject": True})
    selection.write_text(json.dumps(payload), encoding="utf-8")

    symbols, error = sra.selected_peer_symbols(data_root / "SUBJ")

    assert error is None
    assert symbols == ["BA", "LMT"]


def test_peer_metric_kinds_are_the_four_the_table_needs() -> None:
    assert sra.PEER_METRIC_KINDS == ["profile", "prices", "financials", "technical"]


def test_run_creates_each_peer_tree_and_reports_per_peer(
        tmp_path: Path, monkeypatch, capsys) -> None:
    data_root = _subject(tmp_path, ["BA", "LMT"])
    calls: list[str] = []

    def fake_run_prefetch(ticker, ticker_dir, state, kinds, fetchers, deps, **kw):
        calls.append(ticker)
        return {"fetched": list(kinds), "skipped": [], "errors": {}, "warnings": {}}

    monkeypatch.setattr(sra, "run_prefetch", fake_run_prefetch)
    args = _args(data_root)

    assert args.fn(args) == 0
    assert calls == ["BA", "LMT"]
    assert (data_root / "BA" / ".state.json").exists()
    assert (data_root / "BA" / "structured").is_dir()

    assert sorted(json.loads(capsys.readouterr().out)["peers"]) == ["BA", "LMT"]


def test_failing_peer_is_a_warning_not_a_failure(
        tmp_path: Path, monkeypatch, capsys) -> None:
    data_root = _subject(tmp_path, ["BA", "LMT"])

    def fake_run_prefetch(ticker, ticker_dir, state, kinds, fetchers, deps, **kw):
        if ticker == "BA":
            return {"fetched": [], "skipped": [],
                    "errors": {"profile": "boom"}, "warnings": {}}
        return {"fetched": list(kinds), "skipped": [], "errors": {}, "warnings": {}}

    monkeypatch.setattr(sra, "run_prefetch", fake_run_prefetch)
    args = _args(data_root)

    assert args.fn(args) == 0
    assert any("BA" in w for w in json.loads(capsys.readouterr().out)["warnings"])


def test_an_illegal_symbol_is_skipped_with_a_warning(
        tmp_path: Path, monkeypatch, capsys) -> None:
    data_root = _subject(tmp_path, ["../evil", "LMT"])
    monkeypatch.setattr(sra, "run_prefetch",
                        lambda *a, **k: {"fetched": [], "skipped": [],
                                         "errors": {}, "warnings": {}})
    args = _args(data_root)

    assert args.fn(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["peers"] == ["LMT"]
    assert any("EVIL" in w and "not a valid ticker" in w for w in out["warnings"])
    assert not (data_root / "..").resolve().joinpath("evil").exists()
