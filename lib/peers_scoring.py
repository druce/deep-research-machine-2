#!/usr/bin/env python3
"""Peer selection: the user's picks first, then the model's ranking.

The four-axis weighted composite this module used to run is retired. A formula
over model-assigned integers added arithmetic precision to a judgment call
without adding accuracy -- and it could not absorb the noise the mechanical
sources produce (a broad Consumer-Discretionary ETF put ABNB and EXPE above F
and GM for TSLA). The model now returns an ordered top 5 directly; Python only
enforces the pinning contract and preserves provenance.
"""
from __future__ import annotations

# The peer-set size, and the single source of truth for the number 5. It is also
# the threshold at which selection is skipped: naming this many peers fills every
# slot. lib/fetchers/peers.py binds MIN_USER_PEERS to this.
PEER_SET_SIZE = 5


def _norm(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def _row(base: dict, ranking: dict | None) -> dict:
    """A selection row: the candidate plus the model's rank and rationale.

    `rank` is None for a pinned peer the model did not rank -- it was not judged
    poorly, it was not judged at all.
    """
    return {**base,
            "rank": (ranking or {}).get("rank"),
            "rationale": (ranking or {}).get("rationale", "")}


def _rank_key(entry: dict) -> tuple[int, int]:
    """Sort key for the model's ranking: valid ints ascending, then everything
    else -- so the `rank` field and the artifact's row order can never disagree.
    Python's sort is stable, so ties (equal or equally-invalid rank) keep the
    order `entry` arrived in."""
    rank = entry.get("rank")
    if isinstance(rank, int) and not isinstance(rank, bool):
        return (0, rank)
    return (1, 0)


def _clean_ranked(ranked: object) -> list[dict]:
    """Normalize the model's `ranked` output into a deduped list of dicts, each
    with a non-blank, normalized `symbol`.

    `ranked` is model-authored JSON, not a structure this module controls: a
    non-list payload, a non-dict element, or an entry with a missing/blank/null
    `symbol` must be dropped rather than crash `apply_selection` -- `main()`
    returns an exit code, it does not traceback on a model's output. A symbol
    repeated in any case/whitespace variant keeps only its first occurrence, so
    a duplicated ranking cannot duplicate a peer in the selected set.
    """
    cleaned: list[dict] = []
    seen: set[str] = set()
    entries = ranked if isinstance(ranked, list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        symbol = _norm(entry.get("symbol"))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        cleaned.append({**entry, "symbol": symbol})
    return cleaned


def apply_selection(
    candidates: list[dict],
    ranked: list[dict],
    pinned: list[str] | tuple[str, ...] = (),
    top_n: int = PEER_SET_SIZE,
) -> tuple[list[dict], list[dict]]:
    """(selected, runners_up) under the top-up contract.

    `pinned` are the user's own peers: they take their slots in the order given,
    deduped, regardless of the model's opinion and even when absent from
    `candidates`. The model's `ranked` list fills whatever remains, ordered by
    its `rank` field (missing/non-integer ranks sort last).

    A ranked symbol absent from `candidates` is kept too -- the model reads the
    proxy excerpt and may name a peer no mechanical source surfaced, which is
    the point of showing it the prose. The subject is never selectable, and
    candidate/ranked symbols are both normalized before comparison so that
    holds independently of how the candidate table capitalized things.

    `top_n` binds the PINNED peers too: a user naming more than `top_n` gets the
    first `top_n`, and the extras become runners-up ahead of the model's
    (§13.4). Returning every pinned symbol would make `apply_selection` violate
    its own `top_n` contract, and pre-trimming in the caller -- the shape this
    replaced -- silently dropped those extras instead of recording them.
    """
    by_symbol = {_norm(r.get("symbol")): r for r in candidates}
    subject = {_norm(r.get("symbol")) for r in candidates if r.get("is_subject")}

    cleaned = _clean_ranked(ranked)
    cleaned.sort(key=_rank_key)
    rankings = {r["symbol"]: r for r in cleaned}

    pinned_syms = list(dict.fromkeys(
        _norm(s) for s in pinned if _norm(s))) if pinned else []
    pinned_syms = [s for s in pinned_syms if s not in subject]
    # `rest` excludes every pinned symbol, not just the seated ones: an extra
    # the model also ranked must appear once, as the user's, not twice.
    seated, extras = pinned_syms[:top_n], pinned_syms[top_n:]

    def build(symbol: str) -> dict:
        base = by_symbol.get(symbol, {"symbol": symbol, "is_subject": False})
        return _row(base, rankings.get(symbol))

    selected = [build(s) for s in seated]
    rest = [build(r["symbol"]) for r in cleaned
            if r["symbol"] not in pinned_syms and r["symbol"] not in subject]

    fill = max(0, top_n - len(selected))
    return selected + rest[:fill], [build(s) for s in extras] + rest[fill:]
