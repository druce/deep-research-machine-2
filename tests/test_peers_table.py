"""Merging the four peer sources, then the overlap and hygiene filters."""
from lib.peers_table import hygiene_filter, merge_candidates, overlap_filter


def _merged():
    return merge_candidates(
        subject="CRWD",
        user=["NET", "PANW"],
        fmp=["PANW", "ACN", "ADI"],
        proxy=[{"name": "Zscaler, Inc.", "symbol": "ZS"},
               {"name": "Snap Inc.", "symbol": "SNAP"}],
        fund_counts={"PANW": 3, "ZS": 3, "S": 2, "LMT": 1, "MRNA": 1, "CRWD": 4},
    )


def test_merge_records_every_source_that_named_a_candidate():
    """`sources` is an ordered list; canonical order is user, fmp_peers, proxy, funds."""
    rows = {r["symbol"]: r for r in _merged()}
    assert rows["PANW"]["sources"] == ["user", "fmp_peers", "funds"]
    assert rows["PANW"]["fund_count"] == 3
    assert rows["ACN"]["sources"] == ["fmp_peers"]
    assert rows["ACN"]["fund_count"] == 0
    assert rows["SNAP"]["sources"] == ["proxy"]
    assert rows["S"]["sources"] == ["funds"]


def test_merge_includes_the_subject_row_flagged():
    rows = {r["symbol"]: r for r in _merged()}
    assert rows["CRWD"]["is_subject"] is True
    assert all(r["is_subject"] is False for r in _merged() if r["symbol"] != "CRWD")


def test_merge_normalizes_and_dedupes_symbols():
    rows = merge_candidates("crwd", user=[" panw ", "panw"], fmp=["PANW"],
                            proxy=[], fund_counts={})
    assert [r["symbol"] for r in rows] == ["CRWD", "PANW"]


def test_overlap_filter_drops_single_fund_names_nobody_else_named():
    kept = {r["symbol"] for r in overlap_filter(_merged())}
    assert "LMT" not in kept and "MRNA" not in kept   # 1 fund, no other source
    assert "S" in kept                                 # 2 funds
    assert "ACN" in kept                               # 0 funds but FMP named it
    assert "SNAP" in kept                              # 0 funds but proxy named it
    assert "NET" in kept                               # 0 funds but user named it


def test_overlap_filter_never_drops_the_subject():
    rows = merge_candidates("CRWD", user=[], fmp=[], proxy=[], fund_counts={"CRWD": 1})
    assert [r["symbol"] for r in overlap_filter(rows)] == ["CRWD"]


def test_hygiene_filter_drops_funds_etfs_and_dead_listings():
    rows = [
        {"symbol": "PANW", "is_etf": False, "is_fund": False, "is_actively_trading": True},
        {"symbol": "CIBR", "is_etf": True,  "is_fund": False, "is_actively_trading": True},
        {"symbol": "XFUND", "is_etf": False, "is_fund": True, "is_actively_trading": True},
        {"symbol": "SPLK", "is_etf": False, "is_fund": False, "is_actively_trading": False},
    ]
    assert [r["symbol"] for r in hygiene_filter(rows)] == ["PANW"]


def test_hygiene_filter_never_drops_the_subject():
    """I2: `overlap_filter` exempts the subject and `hygiene_filter` did not.

    A subject whose FMP profile reports isActivelyTrading=false was deleted from
    its own candidate table, so SKILL.md's "the row with is_subject: true is the
    SUBJECT" pointed at nothing and `apply_selection`'s subject set came back
    empty -- letting the model rank the subject as its own peer.
    """
    rows = [
        {"symbol": "CRWD", "is_subject": True, "is_etf": False, "is_fund": False,
         "is_actively_trading": False},
        {"symbol": "PANW", "is_subject": False, "is_etf": False, "is_fund": False,
         "is_actively_trading": True},
    ]
    assert [r["symbol"] for r in hygiene_filter(rows)] == ["CRWD", "PANW"]


def test_hygiene_filter_keeps_rows_with_unknown_flags():
    """A profile lookup that returned nothing must not silently delete a candidate."""
    rows = [{"symbol": "PANW", "is_etf": None, "is_fund": None,
             "is_actively_trading": None}]
    assert [r["symbol"] for r in hygiene_filter(rows)] == ["PANW"]
