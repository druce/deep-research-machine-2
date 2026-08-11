"""`peers-candidates` and `peers-select` (spec §13.3–13.5, §24 peers block).

Selection is DETERMINISTIC: Python never scores anything. The model returns an
ordered list, the user's own picks are pinned ahead of it, and everything here
tests that contract — pinning, the five-slot limit, rank-order fill, subject
exclusion, and the two staleness guards that keep a ranking or a pinned list
from an unrelated earlier run leaking into this one.

The §24 block additionally requires that a PROXY-ONLY ranked candidate is
selectable: the rater reads the DEF 14A prose and may name a peer no mechanical
source surfaced, so a ranked row carrying only {symbol, rank, rationale} has to
survive into the selected set. That is the whole reason the excerpt is shown.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sra
from lib.fetchers.peers import peers_path, write_user_peers
from lib.peers_scoring import PEER_SET_SIZE
from lib.provenance import StructuredMeta, read_structured, write_derived
from lib.statefile import load_state
from lib.validate import validate

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=10)
LATER = NOW + timedelta(days=10)


def _init(tmp_path: Path) -> Path:
    sra.main(["init", "CRWD", "--data-root", str(tmp_path)])
    return tmp_path / "CRWD"


def write_candidates(ticker_dir: Path, symbols: list[str], *,
                     changed_at: datetime = NOW, subject: str = "CRWD") -> Path:
    """A minimal peers_candidates.json in the shape the gather writes."""
    rows = [{"symbol": subject, "name": f"{subject} Inc.", "fund_count": 0,
             "sources": [], "is_subject": True}]
    rows += [{"symbol": s, "name": f"{s} Inc.", "fund_count": 2,
              "sources": ["fmp_peers"], "is_subject": False,
              "fmp_sector": "Technology", "market_cap": 1_000,
              "revenue_ttm": 1_000_000, "description": f"{s} does things."}
             for s in symbols]
    meta = StructuredMeta(
        id="peers_candidates", ticker=subject, producer="compute",
        title=f"{subject} peer candidate table", source="composite",
        provider_tool="lib/fetchers/peers.py",
        fetch_cmd=f"uv run python sra.py peers-candidates {subject}",
        computed_at=NOW.isoformat(), as_of=NOW.date().isoformat(),
        derived_from=["peers_fmp"])
    # peers_fmp has to exist for validate's derivation check to resolve.
    write_derived(ticker_dir, StructuredMeta(
        id="peers_fmp", ticker=subject, producer="fetch",
        title=f"{subject} FMP stock peers", source="Financial Modeling Prep",
        url="https://financialmodelingprep.com/stable/stock-peers",
        provider_tool="FMP /stable/stock-peers",
        fetch_cmd=f"uv run python sra.py peers-candidates {subject}",
        fetched_at=NOW.isoformat(), as_of=NOW.date().isoformat()),
        {"peers": [{"symbol": s, "origin": "fmp"} for s in symbols]},
        namespace="peers")
    return write_derived(ticker_dir, meta,
                         {"subject": subject, "funds_used": [], "candidates": rows,
                          "candidates_changed_at": changed_at.isoformat()},
                         namespace="peers")


def write_ranked(ticker_dir: Path, entries: list[dict], *,
                 generated_at: datetime = NOW, path: Path | None = None) -> Path:
    """peers_ranked.json as the sra-rater writes it: `model` shape, ordered data."""
    meta = StructuredMeta(
        id="peers_ranked", ticker="CRWD", producer="model",
        title="CRWD ranked peer candidates", source="sra-rater",
        generated_at=generated_at.isoformat(), as_of=generated_at.date().isoformat(),
        derived_from=["peers_candidates"])
    if path is None:
        return write_derived(ticker_dir, meta, entries, namespace="peers")
    # An explicit --ranked-file target, written by hand in the same shape.
    payload = {"_meta": {"id": "peers_ranked", "ticker": "CRWD", "producer": "model",
                         "title": "ranked", "source": "sra-rater",
                         "generated_at": generated_at.isoformat(),
                         "as_of": generated_at.date().isoformat(),
                         "derived_from": ["peers_candidates"]},
               "data": entries}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def ranked_rows(*symbols: str) -> list[dict]:
    return [{"symbol": s, "rank": i, "rationale": f"{s} is comparable"}
            for i, s in enumerate(symbols, start=1)]


def run_select(tmp_path: Path, *args) -> int:
    return sra.main(["peers-select", "CRWD", "--data-root", str(tmp_path), *args])


def selected(ticker_dir: Path) -> dict:
    _meta, data = read_structured(peers_path(ticker_dir, "peers_selected"))
    return data


def _errors(ticker_dir: Path):
    return [f for f in validate(ticker_dir, ticker_dir.parent) if f.severity == "error"]


# --- peers-candidates ------------------------------------------------------

@pytest.fixture
def stub_gather(monkeypatch):
    """Replace fetch_peers so peers-candidates is exercised without a provider."""
    calls: list[dict] = []

    def fake(ticker, ticker_dir, state, *, user_peers=None, top_funds=None, **kw):
        calls.append({"ticker": ticker, "user_peers": user_peers,
                      "top_funds": top_funds})
        if user_peers:
            write_user_peers(ticker_dir, [s.upper() for s in user_peers], NOW)
            state.setdefault("derived", {}).setdefault("peers_selected", {})[
                "user_peers"] = [s.upper() for s in user_peers]
        return True, [], None

    monkeypatch.setattr(sra, "fetch_peers", fake)
    return calls


def test_candidates_threads_the_peers_flag(tmp_path: Path, stub_gather, capsys):
    _init(tmp_path)
    capsys.readouterr()          # drop init's line so stdout is just the JSON
    assert sra.main(["peers-candidates", "CRWD", "--data-root", str(tmp_path),
                     "--peers", "panw, zs ,"]) == 0
    assert stub_gather[0]["user_peers"] == ["panw", " zs "]
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_candidates_threads_top_funds(tmp_path: Path, stub_gather):
    _init(tmp_path)
    assert sra.main(["peers-candidates", "CRWD", "--data-root", str(tmp_path),
                     "--top-funds", "3"]) == 0
    assert stub_gather[0]["top_funds"] == 3


def test_candidates_persists_user_peers_into_state(tmp_path: Path, stub_gather):
    """§13.5: state is what a later refresh reconstructs the pinned list from."""
    d = _init(tmp_path)
    sra.main(["peers-candidates", "CRWD", "--data-root", str(tmp_path),
              "--peers", "PANW,ZS"])
    assert load_state(d)["derived"]["peers_selected"]["user_peers"] == ["PANW", "ZS"]


def test_candidates_exits_2_when_every_source_failed(tmp_path: Path, monkeypatch):
    _init(tmp_path)
    monkeypatch.setattr(sra, "fetch_peers",
                        lambda *a, **kw: (False, [], "no peers found for CRWD"))
    assert sra.main(["peers-candidates", "CRWD", "--data-root", str(tmp_path)]) == 2


def test_candidates_needs_an_initialized_ticker(tmp_path: Path):
    assert sra.main(["peers-candidates", "CRWD", "--data-root", str(tmp_path)]) == 1


# --- peers-select: the pinning contract -----------------------------------

def test_user_peers_are_pinned_first_in_user_order(tmp_path: Path):
    """§13.3 step 1: pinned peers take their slots in the order given, ahead of
    whatever the model thinks."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA", "NET"])
    write_user_peers(d, ["NET", "OKTA"], NOW)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S"))
    assert run_select(tmp_path) == 0
    assert [r["symbol"] for r in selected(d)["peers"]][:2] == ["NET", "OKTA"]


def test_ranking_order_fills_the_remaining_slots(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA", "NET"])
    write_user_peers(d, ["NET"], NOW)
    write_ranked(d, ranked_rows("FTNT", "PANW", "ZS", "S", "OKTA"))
    assert run_select(tmp_path) == 0
    assert [r["symbol"] for r in selected(d)["peers"]] == [
        "NET", "FTNT", "PANW", "ZS", "S"]


def test_selection_stops_at_five(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA", "NET", "TENB"])
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S", "OKTA", "NET", "TENB"))
    assert run_select(tmp_path) == 0
    data = selected(d)
    assert len(data["peers"]) == PEER_SET_SIZE
    assert [r["symbol"] for r in data["runners_up"]] == ["NET", "TENB"]


def test_rank_field_and_row_order_cannot_disagree(tmp_path: Path):
    """The artifact's order IS the ranking, so an out-of-order `rank` sorts."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT"])
    write_ranked(d, [{"symbol": "ZS", "rank": 3, "rationale": "c"},
                     {"symbol": "PANW", "rank": 1, "rationale": "a"},
                     {"symbol": "FTNT", "rank": 2, "rationale": "b"}])
    assert run_select(tmp_path) == 0
    assert [r["symbol"] for r in selected(d)["peers"]] == ["PANW", "FTNT", "ZS"]


def test_the_subject_is_never_selectable(tmp_path: Path):
    """§13.2: the subject row exists for comparison and can never be selected —
    even when the model ranks it."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S"])
    write_ranked(d, ranked_rows("CRWD", "PANW", "ZS", "FTNT", "S"))
    assert run_select(tmp_path) == 0
    data = selected(d)
    assert "CRWD" not in [r["symbol"] for r in data["peers"]]
    assert "CRWD" not in [r["symbol"] for r in data["runners_up"]]


def test_a_pinned_subject_is_also_refused(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"])
    write_user_peers(d, ["CRWD", "PANW"], NOW)
    write_ranked(d, ranked_rows("ZS", "FTNT", "S", "OKTA"))
    assert run_select(tmp_path) == 0
    assert [r["symbol"] for r in selected(d)["peers"]][0] == "PANW"


def test_a_proxy_only_ranked_candidate_is_selectable(tmp_path: Path):
    """§24: a ranked row carrying ONLY {symbol, rank, rationale}.

    The rater reads the DEF 14A prose and may name a peer no mechanical source
    surfaced — which is the entire point of showing it the excerpt. Requiring a
    candidate-table row would silently discard exactly those picks.
    """
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    write_ranked(d, [{"symbol": "OKTA", "rank": 1, "rationale": "named in the proxy"},
                     {"symbol": "PANW", "rank": 2, "rationale": "b"},
                     {"symbol": "ZS", "rank": 3, "rationale": "c"}])
    assert run_select(tmp_path) == 0
    rows = selected(d)["peers"]
    assert [r["symbol"] for r in rows] == ["OKTA", "PANW", "ZS"]
    okta = rows[0]
    assert okta["rationale"] == "named in the proxy"
    assert okta["is_subject"] is False


# --- §13.4 top-up behavior -------------------------------------------------

def test_five_user_peers_short_circuit_the_rater(tmp_path: Path):
    """§13.4: ≥5 user peers -> rater skipped, first five win. No ranking is even
    read, so selection must succeed with no peers_ranked.json on disk."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    write_user_peers(d, ["A", "B", "C", "D", "E", "F"], NOW)
    assert not peers_path(d, "peers_ranked").exists()
    assert run_select(tmp_path) == 0
    data = selected(d)
    assert [r["symbol"] for r in data["peers"]] == ["A", "B", "C", "D", "E"]
    assert data["origin"] == "user_provided"


def test_user_extras_beyond_five_are_recorded_as_runners_up(tmp_path: Path):
    """§13.4: extras remain recorded, with origin user_provided."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW"])
    write_user_peers(d, ["A", "B", "C", "D", "E", "F", "G"], NOW)
    assert run_select(tmp_path) == 0
    data = selected(d)
    assert [r["symbol"] for r in data["runners_up"]] == ["F", "G"]
    assert all(r["origin"] == "user_provided" for r in data["runners_up"])


def test_a_short_set_is_warned_about_not_silently_accepted(tmp_path: Path):
    """The premise is five comparables; a short set reporting success is the
    same silent-shortfall shape as a source that vanishes."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    write_ranked(d, ranked_rows("PANW", "ZS"))
    assert run_select(tmp_path) == 0
    assert any("2 of 5" in w for w in selected(d)["warnings"])


# --- §13.5 staleness guards ------------------------------------------------

def test_a_ranking_that_predates_the_candidate_set_exits_1(tmp_path: Path):
    """§13.5: it ranked an older table, so its ordering is not about this one."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT"], changed_at=NOW)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT"), generated_at=EARLIER)
    assert run_select(tmp_path) == 1
    assert not peers_path(d, "peers_selected").exists()


def test_a_ranking_newer_than_the_candidate_set_is_accepted(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT"], changed_at=NOW)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT"), generated_at=LATER)
    assert run_select(tmp_path) == 0


def test_a_re_gather_that_changed_nothing_does_not_invalidate_a_ranking(
        tmp_path: Path):
    """`peers` is a default prefetch kind, so the table is rewritten by routine
    refreshes. The guard keys off candidates_changed_at — when the candidate SET
    last changed — precisely so a no-op refresh does not reject a valid ranking.
    """
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT"], changed_at=EARLIER)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT"), generated_at=NOW)
    assert run_select(tmp_path) == 0


def test_a_stale_peers_user_falls_back_to_state(tmp_path: Path):
    """§13.5: a peers_user.json older than the stamp cannot silently pin slots;
    selection falls back to state.derived.peers_selected.user_peers."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"], changed_at=NOW)
    write_user_peers(d, ["GHOST"], EARLIER)          # left by an unrelated run
    state = load_state(d)
    state.setdefault("derived", {})["peers_selected"] = {"user_peers": ["OKTA"]}
    (d / ".state.json").write_text(json.dumps(state), encoding="utf-8")
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S"))

    assert run_select(tmp_path) == 0
    data = selected(d)
    symbols = [r["symbol"] for r in data["peers"]]
    assert "GHOST" not in symbols
    assert symbols[0] == "OKTA"                       # the state list pinned instead
    assert any("stale" in w for w in data["warnings"])


def test_a_stale_peers_user_with_no_state_fallback_pins_nothing(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"], changed_at=NOW)
    write_user_peers(d, ["GHOST"], EARLIER)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S", "OKTA"))
    assert run_select(tmp_path) == 0
    assert "GHOST" not in [r["symbol"] for r in selected(d)["peers"]]


def test_nothing_is_deleted_by_the_stale_path(tmp_path: Path):
    """§13.5: "Nothing is deleted; re-assert a list with peers-candidates --peers"."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"], changed_at=NOW)
    user_path = write_user_peers(d, ["GHOST"], EARLIER)
    before = user_path.read_text(encoding="utf-8")
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S", "OKTA"))
    run_select(tmp_path)
    assert user_path.read_text(encoding="utf-8") == before


# --- failure modes ---------------------------------------------------------

def test_no_candidate_table_exits_1(tmp_path: Path):
    _init(tmp_path)
    assert run_select(tmp_path) == 1


def test_a_short_user_list_with_no_ranking_exits_1(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    write_user_peers(d, ["PANW"], NOW)
    assert run_select(tmp_path) == 1


def test_a_malformed_ranking_exits_1_without_a_traceback(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    peers_path(d, "peers_ranked").write_text("{not json", encoding="utf-8")
    assert run_select(tmp_path) == 1


def test_a_ranking_without_a_meta_envelope_exits_1(tmp_path: Path):
    """§13.5's guard reads `_meta.generated_at`; a bare list has no stamp, so
    accepting one would silently skip the staleness check."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    peers_path(d, "peers_ranked").write_text(
        json.dumps(ranked_rows("PANW", "ZS")), encoding="utf-8")
    assert run_select(tmp_path) == 1


def test_a_ranking_whose_data_is_not_a_list_exits_1(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS"])
    write_derived(d, StructuredMeta(
        id="peers_ranked", ticker="CRWD", producer="model", title="r",
        source="sra-rater", generated_at=NOW.isoformat(),
        as_of=NOW.date().isoformat(), derived_from=["peers_candidates"]),
        {"not": "a list"}, namespace="peers")
    assert run_select(tmp_path) == 1


def test_select_needs_an_initialized_ticker(tmp_path: Path):
    assert run_select(tmp_path) == 1


def test_ranked_file_flag_reads_an_explicit_path(tmp_path: Path):
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT"])
    external = tmp_path / "ranking.json"
    write_ranked(d, ranked_rows("FTNT", "PANW", "ZS"), path=external)
    assert run_select(tmp_path, "--ranked-file", str(external)) == 0
    assert [r["symbol"] for r in selected(d)["peers"]][0] == "FTNT"


# --- the written artifact --------------------------------------------------

def test_peers_selected_is_silver_model_shaped_and_validates(tmp_path: Path):
    """§13.3: it records model-mediated judgment, so `model` shape — and it
    lands in derived/peers/, never structured/, because §13.6 makes it lineage
    rather than evidence."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"])
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S", "OKTA"))
    assert run_select(tmp_path) == 0
    meta, _ = read_structured(peers_path(d, "peers_selected"))
    assert meta.producer == "model"
    assert not meta.url and not meta.fetch_cmd
    assert set(meta.derived_from) == {"peers_candidates", "peers_ranked"}
    assert not (d / "structured" / "peers_selected.json").exists()
    assert _errors(d) == []


def test_peers_selected_records_rank_and_rationale(tmp_path: Path):
    """§13.3: the artifact stores rank and rationale — it is why these five."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"])
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S", "OKTA"))
    run_select(tmp_path)
    first = selected(d)["peers"][0]
    assert first["rank"] == 1
    assert first["rationale"] == "PANW is comparable"


def test_a_pinned_peer_the_model_never_ranked_has_a_null_rank(tmp_path: Path):
    """It was not judged poorly; it was not judged at all."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA", "NET"])
    write_user_peers(d, ["NET"], NOW)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S"))
    run_select(tmp_path)
    net = next(r for r in selected(d)["peers"] if r["symbol"] == "NET")
    assert net["rank"] is None and net["rationale"] == ""


def test_select_records_the_peers_kind_in_state(tmp_path: Path):
    """§13.3: `peers` is the SELECTED set — this is the stage the gather must
    never stamp, and the one `prefetch --stale-only` reads."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"])
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S", "OKTA"))
    run_select(tmp_path)
    state = load_state(d)
    assert state["data"]["peers"]["current_ids"] == ["peers_selected"]
    assert state["data"]["peers"]["policy_days"] == 90
    assert state["derived"]["peers_selected"]["current_id"] == "peers_selected"


def test_select_preserves_the_persisted_user_peers(tmp_path: Path):
    """`record_derived` replaces `derived[key]` wholesale, and §13.5 keeps the
    pinned list at `derived.peers_selected.user_peers` — so writing the lineage
    entry must not wipe the very list a later refresh reconstructs from."""
    d = _init(tmp_path)
    write_candidates(d, ["PANW", "ZS", "FTNT", "S", "OKTA"])
    state = load_state(d)
    state.setdefault("derived", {})["peers_selected"] = {
        "user_peers": ["OKTA"], "asked_at": NOW.isoformat()}
    (d / ".state.json").write_text(json.dumps(state), encoding="utf-8")
    write_user_peers(d, ["OKTA"], NOW)
    write_ranked(d, ranked_rows("PANW", "ZS", "FTNT", "S"))

    assert run_select(tmp_path) == 0
    after = load_state(d)["derived"]["peers_selected"]
    assert after["user_peers"] == ["OKTA"]
    assert after["asked_at"] == NOW.isoformat()
    assert after["current_id"] == "peers_selected"
