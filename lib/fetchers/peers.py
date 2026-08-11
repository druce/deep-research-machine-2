#!/usr/bin/env python3
"""Peers: gather four independent sources and merge into one candidate table (§13).

The user's list, FMP stock-peers and fund-overlap are each independent voters
into the candidate table -- none tops up or dedupes against another. The DEF
14A contributes prose, not symbols: its excerpt is written to `peers_proxy.json`
for the `sra-rater` subagent to read; `sra.py peers-select` then applies that
ranking and tops up to the final peer set.

EVERY artifact here is SILVER (§4.2, §13.3): they land under `derived/peers/`
via `write_derived`, never `structured/`. That is a layer statement, not a
storage preference -- a comparables table in the report cites each peer's OWN
bronze evidence (§13.6), and these files record only WHY the peers were chosen.
Putting them under `structured/` would make a citation resolve into peer-
selection lineage, which is exactly the confusion §1.2 exists to prevent.

Producer shapes (§6.2), per artifact:

- `peers_fmp`, `peers_funds`, `peers_proxy` -> `fetch`: each is one provider's
  answer, with the endpoint in `url` and the query in `request`.
- `peers_candidates` -> `compute`: merged and enriched from the above, which it
  names in `derived_from`.
- `peers_user` -> NEITHER. It is a list the user typed, so it has no url and no
  antecedent artifact, and no §6.2 shape fits it. It is written as a bare JSON
  record and `validate` skips it, the same treatment `*.urls.json` gets --
  claiming a `fetch` or `compute` shape would make its provenance assert
  something false about where the list came from.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lib.fmp_http import fmp_get, sanitize
from lib.peers_enrich import enrich, fetch_profiles, fetch_revenues
from lib.peers_funds import (
    MAX_HOLDINGS,
    MIN_HOLDINGS,
    TOP_FUNDS,
    fmp_etf_holdings_count,
    prefilter_exposure,
    select_funds,
    union_holdings,
)
from lib.peers_proxy import fetch_proxy_excerpt
from lib.peers_scoring import PEER_SET_SIZE
from lib.peers_table import hygiene_filter, merge_candidates, overlap_filter
from lib.provenance import StructuredMeta, write_derived
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

# The silver namespace every peers artifact lives in: derived/peers/ (§4.2).
PEERS_NAMESPACE = "peers"

# FMP's `/api/v4/stock_peers` (and every other legacy /api/v3|v4 path) now answers
# 403 "Legacy Endpoint ... only available for legacy users who have valid
# subscriptions prior August 31, 2025". The live replacement is the /stable/
# surface, which returns a FLAT list of {symbol, companyName, price, mktCap}
# rows -- not the legacy `[{"peersList": [...]}]` envelope.
FMP_PEERS_URL = "https://financialmodelingprep.com/stable/stock-peers"
FMP_PROVIDER_TOOL = "FMP /stable/stock-peers"

# The user naming this many peers fills every slot, so `/sra-peers` skips the
# rater fan-out. Same number as the peer-set size, for the same reason.
MIN_USER_PEERS = PEER_SET_SIZE
HTTP_TIMEOUT = 30

# Verified live 2026-08-01. `/stable/etf/holder` (an earlier guess) 404s; the
# asset-exposure surface is the one that answers "which funds hold this stock",
# and returns 1991 rows for CRWD. Both surfaces name the ETF in `symbol` and the
# held security in `asset` -- so on the exposure endpoint the ETF we want is
# `symbol`, NOT `etfSymbol` (which does not exist).
FMP_EXPOSURE_URL = "https://financialmodelingprep.com/stable/etf/asset-exposure"
FMP_HOLDINGS_URL = "https://financialmodelingprep.com/stable/etf/holdings"

# §13.5. `peers-candidates` records state kind `peers_candidates`, NEVER `peers`:
# that key is the SELECTED set, written by `peers-select`.
PEERS_POLICY_DAYS = 90
STATE_KIND = "peers_candidates"


def peers_cmd(ticker: str) -> str:
    """The command that reproduces the peers gather (§6.2 `fetch_cmd`)."""
    return f"uv run python sra.py peers-candidates {ticker.upper()}"


def _pct(value: float | int | str | None) -> float | None:
    """FMP reports weights in PERCENT (9.15 means 9.15%); the filters use fractions.

    Verified against all nine funds in tests/fixtures/etf_exposure_crwd.json:
    raw `weightPercentage` is exactly 100x the fixture value (CRWL 200.0033 vs
    2.0000329, SPAM 12.33 vs 0.1233, ...). Without this division every fund would
    exceed MAX_SUBJECT_WEIGHT (0.25) and `prefilter_exposure` would drop them all,
    silently emptying source 2.
    """
    return None if value is None else float(value) / 100.0


def _fmp_peers(ticker: str) -> list[str]:
    """Default provider: peer symbols from FMP's stable stock-peers endpoint."""
    payload = fmp_get(FMP_PEERS_URL, {"symbol": ticker}, timeout=HTTP_TIMEOUT)
    return [str(row["symbol"]) for row in payload
            if isinstance(row, dict) and row.get("symbol")]


def _normalize(symbols: list[str], subject: str, exclude: set[str]) -> list[str]:
    """Strip/uppercase/dedupe preserving order; drop subject and excluded."""
    out: list[str] = []
    for s in symbols:
        sym = (s or "").strip().upper()
        if sym and sym != subject and sym not in exclude and sym not in out:
            out.append(sym)
    return out


def _fmp_exposure(ticker: str) -> list[dict]:
    """Funds holding the subject, as {etf_symbol, weight} rows."""
    payload = fmp_get(FMP_EXPOSURE_URL, {"symbol": ticker}, timeout=HTTP_TIMEOUT)
    return [{"etf_symbol": r.get("symbol"), "weight": _pct(r.get("weightPercentage"))}
            for r in payload if isinstance(r, dict)]


def _fmp_fund_holdings(fund: str) -> list[dict]:
    """The fund's own holdings. `symbol` on this endpoint is the FUND itself --
    the held security is `asset` -- so there is no `symbol` fallback here.
    """
    payload = fmp_get(FMP_HOLDINGS_URL, {"symbol": fund}, timeout=HTTP_TIMEOUT)
    return [{"symbol": r.get("asset"), "weight": _pct(r.get("weightPercentage"))}
            for r in payload if isinstance(r, dict)]


def peers_dir(ticker_dir: Path) -> Path:
    """`derived/peers/` — the silver namespace holding every peers artifact."""
    return ticker_dir / "derived" / PEERS_NAMESPACE


def peers_path(ticker_dir: Path, artifact_id: str) -> Path:
    return peers_dir(ticker_dir) / f"{artifact_id}.json"


def write_user_peers(ticker_dir: Path, peers: list[str], now: datetime) -> Path:
    """Record the user's pinned list as a bare JSON artifact (§13.3).

    No `_meta` envelope: a user-typed list is a primary input with no url and no
    antecedent artifact, so no §6.2 producer shape describes it, and asserting
    one would put a false claim in the provenance record. `validate` skips this
    filename for that reason.

    `recorded_at` is what §13.5's staleness rule compares against
    `candidates_changed_at`, so a list left by an unrelated earlier run cannot
    silently pin slots in a later selection.
    """
    target = peers_path(ticker_dir, "peers_user")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"peers": [{"symbol": s, "origin": "user_provided"} for s in peers],
               "recorded_at": now.isoformat()}
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".peers_user.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def read_user_peers(ticker_dir: Path) -> tuple[list[str], str | None]:
    """`(symbols, recorded_at)` from `peers_user.json`, or `([], None)`."""
    path = peers_path(ticker_dir, "peers_user")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["peers"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return [], None
    symbols = [str(r["symbol"]).strip().upper() for r in rows
               if isinstance(r, dict) and r.get("symbol")]
    stamp = payload.get("recorded_at")
    return symbols, stamp if isinstance(stamp, str) else None


def _candidates_changed_at(path: Path, subject: str, symbols: list[str],
                           now: datetime) -> str:
    """When the candidate SET last changed, carried forward across re-gathers.

    The gather rewrites `peers_candidates.json` on every run, and `peers` is a
    default prefetch kind -- so keying `peers-select`'s stale-ranking guard off
    the file's mtime let a routine refresh invalidate a perfectly good ranking.
    A ranking is an ORDERING OVER SYMBOLS, so its identity is the subject plus
    the candidate symbols: a re-gather that surfaces the same universe (only
    prices, weights and market caps moved) leaves this stamp alone, and a
    genuinely different table advances it.
    """
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))["data"]
        prior_symbols = [r.get("symbol") for r in prior["candidates"]
                         if isinstance(r, dict)]
        stamp = prior["candidates_changed_at"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return now.isoformat()
    if prior.get("subject") == subject and prior_symbols == symbols \
            and isinstance(stamp, str):
        return stamp
    return now.isoformat()


def _enrichment_problems(kind: str, asked: list[str], got: dict,
                         failed: list[str]) -> list[str]:
    """Warnings for an enrichment pass that failed, wholly or in part.

    `fetch_profiles`/`fetch_revenues` degrade a bad lookup to "unknown", which is
    right per symbol and blinding in bulk: when every /stable/profile call 401s,
    every candidate reaches the rater with null classification, null size and an
    empty description -- and `hygiene_filter` goes inert because every flag is
    unknown. So a pass that resolved NOTHING is reported even when no call
    raised (a provider answering `200 []` for everything raises nothing), and a
    partial failure is counted.
    """
    if not asked:
        return []
    if not got:
        detail = (f"{len(failed)} of {len(asked)} lookups failed" if failed
                  else f"all {len(asked)} lookups answered with no data")
        return [f"enrichment: no {kind} data for any candidate ({detail})"]
    if failed:
        return [f"enrichment: {len(failed)} of {len(asked)} {kind} lookups failed"]
    return []


def fetch_peers(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    user_peers: list[str] | None = None,
    peers_provider: Callable[[str], list[str]] | None = None,
    exposure_provider: Callable[[str], list[dict]] | None = None,
    holdings_provider: Callable[[str], list[dict]] | None = None,
    info_provider: Callable[[str], int | None] | None = None,
    proxy_provider: Callable[[str], tuple[str, str, str]] | None = None,
    profile_provider: Callable[[str], list[dict]] | None = None,
    income_provider: Callable[[str], list[dict]] | None = None,
    top_funds: int | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Gather four peer sources, enrich, and write the candidate table (§13.3).

    Each source is independently optional: a failure is recorded in the returned
    error string and the run continues on the others (§13.5). Returns False only
    when every source failed AND the user named nobody.
    """
    peers_provider = peers_provider or _fmp_peers
    exposure_provider = exposure_provider or _fmp_exposure
    holdings_provider = holdings_provider or _fmp_fund_holdings
    info_provider = info_provider or fmp_etf_holdings_count
    proxy_provider = proxy_provider or fetch_proxy_excerpt
    now = now or datetime.now(timezone.utc)
    subject = ticker.upper()
    user = _normalize(user_peers or [], subject, set())

    paths: list[Path] = []
    derived: list[str] = []
    problems: list[str] = []

    def _fetch_meta(artifact_id: str, title: str, source: str, url: str,
                    tool: str, request: dict | None = None) -> StructuredMeta:
        """§6.2 `fetch`: one provider's answer, with the query recorded.

        `request` never carries the API key — `fmp_get` appends it internally and
        §5 requires credentials be OMITTED from the record, not masked.
        """
        return StructuredMeta(
            id=artifact_id, ticker=subject, producer="fetch", title=title,
            source=source, url=url, provider_tool=tool,
            fetch_cmd=peers_cmd(subject), request=request,
            fetched_at=now.isoformat(), as_of=now.date().isoformat())

    # --- source 4: the user -------------------------------------------------
    if user:
        paths.append(write_user_peers(ticker_dir, user, now))
        # Named in the candidate table's `derived_from` like any other source:
        # `peers_user` carries no `_meta`, but it IS a file at
        # derived/peers/peers_user.json, so `resolve_artifact` finds it and
        # validate's derivation check (§8.4 check 5) resolves it.
        derived.append("peers_user")
        state.setdefault("derived", {}).setdefault("peers_selected", {})[
            "user_peers"] = list(user)
        state["derived"]["peers_selected"]["asked_at"] = now.isoformat()
    # A run without user peers deliberately does NOT delete an existing
    # peers_user.json. `peers` is one of the default prefetch kinds, so a routine
    # `sra.py prefetch TICKER` reaches this line with no --peers and would
    # destroy a list the user typed by hand. The stale-pinning risk is handled
    # where it belongs, without touching user data: `peers-select` ignores a
    # peers_user.json whose `recorded_at` predates the candidate table it is
    # selecting for (§13.5).

    # --- source 1: FMP stock-peers -----------------------------------------
    fmp: list[str] = []
    try:
        fmp = _normalize(peers_provider(subject), subject, set())
    except Exception as exc:  # noqa: BLE001 - provider errors are data, not crashes
        problems.append(f"FMP peers failed: {exc}")
    else:
        if fmp:
            paths.append(write_derived(ticker_dir, _fetch_meta(
                "peers_fmp", f"{subject} FMP stock peers",
                "Financial Modeling Prep", FMP_PEERS_URL, FMP_PROVIDER_TOOL,
                {"symbol": subject}),
                {"peers": [{"symbol": s, "origin": "fmp"} for s in fmp]},
                namespace=PEERS_NAMESPACE))
            derived.append("peers_fmp")
        else:
            # Attempted and yielded nothing. Silence here is what let the
            # /stable/profile and /stable/etf/holder outages ship as successes.
            problems.append("FMP peers returned no symbols")

    # --- source 3: DEF 14A (prose, not symbols) ---------------------------
    proxy_excerpt = ""
    proxy_url = proxy_date = ""
    try:
        proxy_excerpt, proxy_url, proxy_date = proxy_provider(subject)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"DEF 14A excerpt failed: {exc}")
        proxy_url = proxy_date = ""
    if proxy_excerpt:
        # `fetch` shape requires a non-empty url, and a filing that exposes none
        # still has a canonical home on EDGAR — better a search URL that resolves
        # than an artifact that cannot be written at all.
        url = proxy_url or (
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&company={subject}&type=DEF+14A")
        paths.append(write_derived(ticker_dir, _fetch_meta(
            "peers_proxy", f"{subject} DEF 14A peer-group excerpt",
            "SEC EDGAR DEF 14A", url, "lib/peers_proxy.py"),
            {"excerpt": proxy_excerpt, "filing_date": proxy_date},
            namespace=PEERS_NAMESPACE))
        derived.append("peers_proxy")

    # --- source 2: fund overlap --------------------------------------------
    # `/stable/etf/info` is a SEPARATE endpoint from the exposure feed: it can
    # 403 (plan tier), 429 or time out on every candidate fund while exposure
    # answers fine. select_funds treats each failure as "unknown holdings count"
    # and skips the fund, so without the `info_failed` sink a total outage
    # returns [] -- byte-for-byte the same as "no fund fell inside the band",
    # and the source vanishes at exit 0 with warnings: null.
    funds: list[dict] = []
    fund_counts: dict[str, int] = {}
    info_failed: list[str] = []
    exposed: list[dict] = []
    try:
        exposed = prefilter_exposure(exposure_provider(subject))
        picked = select_funds(exposed, info_provider,
                              top_n=top_funds if top_funds is not None else TOP_FUNDS,
                              failed=info_failed)
        holdings = {f["symbol"]: holdings_provider(f["symbol"]) for f in picked}
        fund_counts = union_holdings(holdings)
        fund_counts.pop(subject, None)   # the subject is not its own fund-peer
        funds = picked
    except Exception as exc:  # noqa: BLE001
        problems.append(f"fund overlap failed: {exc}")
    else:
        if funds:
            paths.append(write_derived(ticker_dir, _fetch_meta(
                "peers_funds", f"{subject} fund overlap",
                "Financial Modeling Prep", FMP_EXPOSURE_URL,
                "FMP /stable/etf/asset-exposure", {"symbol": subject}),
                {"funds": funds, "fund_counts": fund_counts},
                namespace=PEERS_NAMESPACE))
            derived.append("peers_funds")
            if not fund_counts:
                problems.append(f"fund overlap: {len(funds)} funds selected but "
                                f"their holdings named no other symbol")
        elif info_failed:
            problems.append(
                f"fund overlap produced no funds "
                f"({len(info_failed)} of {len(exposed)} /etf/info lookups failed)")
        elif exposed:
            # "reported a holdings count inside the band" covers both the
            # out-of-band funds and the ones that reported no count at all; a
            # fund whose lookup FAILED is never in this branch (see above).
            problems.append("fund overlap produced no funds "
                            f"(none of {len(exposed)} funds reported a holdings "
                            f"count inside the {MIN_HOLDINGS}-{MAX_HOLDINGS} band)")
        else:
            problems.append("fund overlap produced no funds "
                            "(no fund passed the exposure prefilter)")

    # sanitize once, at the only place problems become a returned string: every
    # provider message is second-hand text and `warnings` is printed to stdout
    # and relayed to the user by the skill.
    def _report() -> str | None:
        return sanitize("; ".join(problems)) if problems else None

    if not (user or fmp or proxy_excerpt or fund_counts):
        detail = _report()
        return False, paths, (f"no peers found for {subject}: {detail}" if detail
                              else f"no peers found for {subject}")

    # --- merge, filter, enrich -----------------------------------------------
    # Ordering minimises API calls: classify with profiles first, drop
    # non-comparables via hygiene_filter, THEN spend one income-statement call
    # per SURVIVING candidate -- not per pre-hygiene candidate, since ETFs and
    # delisted shells never need a revenue lookup at all.
    rows = overlap_filter(merge_candidates(subject, user, fmp, [], fund_counts))
    symbols = [r["symbol"] for r in rows]
    profile_failed: list[str] = []
    profiles = fetch_profiles(symbols, profile_fn=profile_provider,
                              failed=profile_failed)
    problems.extend(_enrichment_problems("profile", symbols, profiles,
                                         profile_failed))
    rows = hygiene_filter(enrich(rows, profiles, {}))
    revenue_symbols = [r["symbol"] for r in rows]
    revenue_failed: list[str] = []
    revenues = fetch_revenues(revenue_symbols, income_fn=income_provider,
                              failed=revenue_failed)
    problems.extend(_enrichment_problems(
        "income", revenue_symbols,
        {s: v for s, v in revenues.items() if v is not None}, revenue_failed))
    rows = enrich(rows, profiles, revenues)

    # `compute` (§6.2): merged from the source artifacts it names, so no `url`.
    # `derived_from` is non-empty by construction — the early return above means
    # at least one of user/fmp/proxy/funds contributed, and each of those appends
    # its own id to `derived` when it writes.
    meta = StructuredMeta(
        id="peers_candidates", ticker=subject, producer="compute",
        title=f"{subject} peer candidate table", source="composite",
        provider_tool="lib/fetchers/peers.py", fetch_cmd=peers_cmd(subject),
        computed_at=now.isoformat(), as_of=now.date().isoformat(),
        derived_from=derived)
    changed_at = _candidates_changed_at(
        peers_path(ticker_dir, "peers_candidates"), subject,
        [r["symbol"] for r in rows], now)
    paths.append(write_derived(ticker_dir, meta,
                               {"subject": subject, "funds_used": funds,
                                "candidates": rows,
                                "candidates_changed_at": changed_at},
                               namespace=PEERS_NAMESPACE))
    # NOT under the "peers" kind: that key is the SELECTED peer set, written by
    # `sra.py peers-select`. Stamping the raw 30-row candidate table there made
    # `status` report peers as fetched and made `prefetch --stale-only` skip
    # peers for 90 days even though no peer set was ever selected (§13.3).
    record_fetch(state, STATE_KIND, "peers_candidates", now,
                 {"policy_days": PEERS_POLICY_DAYS})
    return True, paths, _report()
