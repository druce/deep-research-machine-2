"""Selection: pinned user peers first, then the model's ranking."""
import pytest

from lib.peers_scoring import PEER_SET_SIZE, apply_selection


def _cands(*symbols):
    return [{"symbol": s, "is_subject": False, "name": f"{s} Inc.",
             "fund_count": 1, "sources": ["funds"]} for s in symbols]


def _ranked(*pairs):
    return [{"symbol": s, "rank": i + 1, "rationale": r}
            for i, (s, r) in enumerate(pairs)]


def test_selection_follows_the_models_rank_order():
    sel, run = apply_selection(
        _cands("A", "B", "C", "D", "E", "F"),
        _ranked(("C", "best"), ("A", "second"), ("B", "third"),
                ("D", "fourth"), ("E", "fifth"), ("F", "sixth")))
    assert [r["symbol"] for r in sel] == ["C", "A", "B", "D", "E"]
    assert [r["symbol"] for r in run] == ["F"]
    assert len(sel) == PEER_SET_SIZE


def test_selection_carries_rank_and_rationale_onto_the_row():
    sel, _ = apply_selection(_cands("A"), _ranked(("A", "direct competitor")))
    assert sel[0]["rank"] == 1
    assert sel[0]["rationale"] == "direct competitor"
    assert sel[0]["name"] == "A Inc."          # enrichment preserved


def test_pinned_peers_take_their_slots_before_the_ranking():
    sel, _ = apply_selection(
        _cands("A", "B", "C", "D", "E", "F"),
        _ranked(("C", "x"), ("D", "y"), ("E", "z"), ("F", "w")),
        pinned=["A", "B"])
    assert [r["symbol"] for r in sel] == ["A", "B", "C", "D", "E"]


def test_pinned_peers_keep_user_order_and_are_deduped():
    sel, _ = apply_selection(_cands("A", "B"), [], pinned=["b", "A", " B "])
    assert [r["symbol"] for r in sel] == ["B", "A"]


def test_pinned_peer_absent_from_candidates_still_appears():
    sel, _ = apply_selection([], [], pinned=["OBSCURE"])
    assert [r["symbol"] for r in sel] == ["OBSCURE"]
    assert sel[0]["rank"] is None


def test_ranked_symbol_absent_from_candidates_still_appears():
    """The model may name a peer from the proxy excerpt that no source surfaced."""
    sel, _ = apply_selection([], _ranked(("FROMPROXY", "named in the DEF 14A")))
    assert [r["symbol"] for r in sel] == ["FROMPROXY"]
    assert sel[0]["rationale"] == "named in the DEF 14A"


def test_the_subject_is_never_selected():
    cands = _cands("A")
    cands.append({"symbol": "SUBJ", "is_subject": True})
    sel, _ = apply_selection(cands, _ranked(("SUBJ", "itself"), ("A", "real")))
    assert [r["symbol"] for r in sel] == ["A"]


def test_a_pinned_symbol_is_not_repeated_by_the_ranking():
    sel, _ = apply_selection(_cands("A", "B"),
                             _ranked(("A", "x"), ("B", "y")), pinned=["A"])
    assert [r["symbol"] for r in sel] == ["A", "B"]


def test_symbols_are_normalized_on_both_sides():
    sel, _ = apply_selection(_cands("PANW"), _ranked((" panw ", "x")))
    assert [r["symbol"] for r in sel] == ["PANW"]


def test_more_pinned_than_the_set_size_seats_five_and_records_the_extras():
    """§13.4: "first five are selected, extras remain recorded as runners-up".

    EXP returned all seven and left runners empty; its caller then pre-trimmed
    to five, which DISCARDED the extras the spec says to keep. `top_n` binds the
    pinned list here so the function cannot overfill its own contract.
    """
    sel, run = apply_selection(_cands(*"ABCDEFG"), [], pinned=list("ABCDEFG"))
    assert [r["symbol"] for r in sel] == list("ABCDE")
    assert [r["symbol"] for r in run] == list("FG")


def test_a_pinned_extra_the_model_also_ranked_appears_once():
    """It is the user's pick, seated (or shelved) as such — not duplicated into
    the model's half of the runners-up."""
    sel, run = apply_selection(_cands(*"ABCDEFG"),
                               [{"symbol": "F", "rank": 1, "rationale": "x"}],
                               pinned=list("ABCDEF"))
    assert [r["symbol"] for r in sel] == list("ABCDE")
    assert [r["symbol"] for r in run] == ["F"]


def test_retired_names_are_gone():
    import lib.peers_scoring as m
    for gone in ("WEIGHTS", "AXES", "composite", "parse_weights", "select_top"):
        assert not hasattr(m, gone), gone


def test_duplicate_ranked_symbol_is_not_repeated_in_the_artifact():
    """A repeated symbol in the model's ranking (any case) collapses to one
    row -- it must not duplicate a peer and present a four-peer set as five."""
    sel, run = apply_selection(
        _cands("PANW", "ZS"),
        [{"symbol": "PANW", "rank": 1, "rationale": "first"},
         {"symbol": "panw", "rank": 2, "rationale": "dup"},
         {"symbol": "ZS", "rank": 3, "rationale": "third"}])
    assert [r["symbol"] for r in sel] == ["PANW", "ZS"]
    assert run == []


def test_a_ranked_entry_missing_the_symbol_key_is_skipped_not_crashed():
    sel, _ = apply_selection(
        _cands("A"),
        [{"rank": 1, "rationale": "no symbol key at all"},
         {"symbol": "A", "rank": 2, "rationale": "ok"}])
    assert [r["symbol"] for r in sel] == ["A"]


def test_ranked_entries_with_blank_or_null_symbol_are_skipped():
    sel, _ = apply_selection(
        _cands("A"),
        [{"symbol": "", "rank": 1, "rationale": "blank"},
         {"symbol": None, "rank": 2, "rationale": "null"},
         {"symbol": "A", "rank": 3, "rationale": "ok"}])
    assert [r["symbol"] for r in sel] == ["A"]


def test_a_non_list_ranked_payload_does_not_crash():
    """A malformed ranked file (an object instead of a list) must not
    traceback -- it is treated as no ranking at all."""
    sel, run = apply_selection(_cands("A"), {"peers": [{"symbol": "A", "rank": 1}]})
    assert sel == []
    assert run == []


def test_a_non_dict_ranked_element_is_skipped_not_crashed():
    sel, _ = apply_selection(
        _cands("A"), ["A", {"symbol": "A", "rank": 1, "rationale": "ok"}])
    assert [r["symbol"] for r in sel] == ["A"]


def test_selection_orders_by_the_rank_field_not_list_position():
    """The `rank` field decides order, not the position in `ranked` -- so a
    field that contradicts its own list position cannot slip through."""
    sel, _ = apply_selection(
        _cands("A", "B", "C"),
        [{"symbol": "A", "rank": 3, "rationale": "third"},
         {"symbol": "B", "rank": 1, "rationale": "first"},
         {"symbol": "C", "rank": 2, "rationale": "second"}])
    assert [r["symbol"] for r in sel] == ["B", "C", "A"]


def test_a_missing_or_non_integer_rank_sorts_last_and_stays_stable():
    sel, _ = apply_selection(
        _cands("A", "B", "C", "D"),
        [{"symbol": "A", "rank": None, "rationale": "no rank"},
         {"symbol": "B", "rank": 1, "rationale": "first"},
         {"symbol": "C", "rank": "two", "rationale": "bad rank"},
         {"symbol": "D", "rank": 2, "rationale": "second"}])
    assert [r["symbol"] for r in sel] == ["B", "D", "A", "C"]


def test_the_subject_is_excluded_regardless_of_candidate_symbol_case():
    """`is_subject` is keyed off the candidate table's own casing; normalizing
    both sides means the subject cannot slip through on a lowercase symbol."""
    cands = [{"symbol": "crwd", "is_subject": True, "name": "CrowdStrike"}]
    cands += _cands("A")
    sel, _ = apply_selection(cands, _ranked(("crwd", "itself"), ("A", "real")))
    assert [r["symbol"] for r in sel] == ["A"]


def test_pinned_dedup_frees_a_fill_slot():
    """Collapsing duplicate pins must not shrink the fill budget available to
    the model's ranking."""
    sel, run = apply_selection(
        _cands("AAPL", "B", "C"),
        _ranked(("B", "second best"), ("C", "third best")),
        pinned=["AAPL", "aapl", " AAPL "], top_n=3)
    assert [r["symbol"] for r in sel] == ["AAPL", "B", "C"]
    assert run == []
