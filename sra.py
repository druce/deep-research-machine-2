#!/usr/bin/env python3
"""sra6 driver CLI — all deterministic pipeline work lives behind this entry point.

Nothing deterministic belongs in a skill (§3): if it can be a function, it is a
subcommand here. Skills orchestrate model work and call this CLI for everything
mechanical.

See sra6-spec.md §19 for the command surface and §20 for module contracts.
Every §19 command is implemented except `migrate` (§26), an approved deviation:
no legacy corpora are being imported.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from lib.fetchers import fred, multpl
from lib.fetchers.calendar import last_earnings_date
from lib.fetchers.multpl import MULTPL_SERIES
from lib.fetchers.peers import fetch_peers, peers_path, read_user_peers
from lib.fetchers.urls import harvest_answer, harvest_targets
from lib.fetchers.registry import (
    DEFAULT_KINDS,
    FETCHERS,
    KIND_STAGES,
    STAGE_OF,
    dependency_map,
    run_prefetch,
)
from lib.eval_retrieval import DEFAULT_K, compare_to_baseline, evaluate
from lib.grep import grep
from lib.invalidate import apply_invalidation, compute_invalidation
from lib.lock import LockHeldError, TickerLock
from lib.manifest import build_manifest
# `_reject_path_traversal` is imported rather than reimplemented so the rule for
# what counts as a bare artifact id (§8.4) has exactly one definition.
from lib.peers_scoring import PEER_SET_SIZE, apply_selection
from lib.charts.base import load_verdict
from lib.charts.registry import RENDERERS, select
from lib.provenance import (
    StructuredMeta, _reject_path_traversal, read_structured, resolve_artifact,
    resolve_source, write_derived)
from lib.render.assemble import assemble
from lib.render.runs import (
    is_snapshotted, link_latest, resolve_run, run_dirs, write_snapshot)
from lib.questions import (
    DEFAULT_ORIGIN, STATUSES, add_questions, drop_question, load_questions,
    mark_answered, record_attempt)
from lib.sections import SECTION_IDS, load_sections
from lib.statefile import (
    init_state, load_state, mark_section_dirty, record_derived, record_fetch,
    save_state, stale_kinds)
from lib.validate import has_errors, validate
from lib.run_log import build_run_log
from lib.wiki import append_log, update_index, wiki_lint

# Provider credentials (FMP, FRED, OpenAI, Perplexity) live in .env at the repo
# root. Loaded once here, at the single entry point, so every fetcher can just
# read os.environ; load_dotenv never overrides variables already set in the shell.
load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

# §8.4 check 7. Matched against the UPPER-CASED ticker, so `panw` is accepted
# and normalized rather than refused.
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")

# The one reserved directory name exempt from TICKER_RE (§12). No other
# leading-underscore name is accepted — this is a fixed string, not a pattern,
# precisely so `_EVIL` cannot slip through alongside it.
MACRO_TICKER = "_MACRO"

# Macro series name -> FRED series id (§12.1). The friendly name is what the CLI
# takes; `fred_<id_lower>` is the artifact id it produces.
FRED_SERIES: dict[str, str] = {
    "dgs10": "DGS10",       # 10Y Treasury constant maturity
    "dgs2": "DGS2",         # 2Y Treasury constant maturity
    "fedfunds": "FEDFUNDS",  # effective fed funds rate
    "cpiaucsl": "CPIAUCSL",  # CPI, all urban consumers
    "unrate": "UNRATE",      # unemployment rate
    "gdpc1": "GDPC1",        # real GDP
}

# §4. `_MACRO` is shared evidence rather than a research subject, so it gets
# sources + structured only — no wiki, reports, charts or question ledger.
TICKER_SUBDIRS = (
    "sources", "sources/archive", "structured",
    "derived", "derived/answers", "derived/peers",
    "wiki", "wiki/entities", "charts", "charts/candidates",
    "reports", "research",
)
MACRO_SUBDIRS = ("sources", "sources/archive", "structured")


def ticker_dir(data_root: Path, ticker: str) -> Path:
    """Path to the per-ticker knowledge base directory (ticker normalized upper-case)."""
    return data_root / ticker.upper()


def valid_ticker(ticker: str) -> bool:
    """§8.4 check 7. Callers must run this BEFORE touching the filesystem: it is
    what keeps `../evil` or `A/B` from being interpolated into a path at all,
    so path containment holds structurally rather than by later inspection."""
    upper = ticker.upper()
    return upper == MACRO_TICKER or bool(TICKER_RE.match(upper))


def _resolve_ticker(args: argparse.Namespace) -> tuple[str, Path] | None:
    """Validate and normalize the ticker, returning `(ticker, ticker_dir)`, or
    None (after printing the reason) when it must be refused."""
    if not valid_ticker(args.ticker):
        print(
            f"invalid ticker {args.ticker!r}: must match {TICKER_RE.pattern} "
            f"(or be the reserved {MACRO_TICKER})",
            file=sys.stderr,
        )
        return None
    ticker = args.ticker.upper()
    return ticker, ticker_dir(args.data_root, ticker)


def cmd_init(args: argparse.Namespace) -> int:
    """Create the §4 tree, `.state.json` and the wiki stubs. Idempotent.

    The ticker directory is created and locked before anything else is written,
    so a second mutating process cannot half-create the tree alongside the
    first (§7.1). Re-running on an initialized ticker is a no-op that leaves
    the existing state untouched — `init_state` itself refuses to overwrite,
    since that would discard the whole fetch history.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    d.mkdir(parents=True, exist_ok=True)
    try:
        with TickerLock(d, "init", force=args.force_lock):
            subdirs = MACRO_SUBDIRS if ticker == MACRO_TICKER else TICKER_SUBDIRS
            for sub in subdirs:
                (d / sub).mkdir(parents=True, exist_ok=True)

            if (d / ".state.json").exists():
                print(f"{ticker}: already initialized at {d}")
                return 0

            init_state(d, ticker)
            if ticker != MACRO_TICKER:
                (d / "wiki" / "00_index.md").write_text(
                    f"# {ticker} wiki index\n\n(no pages yet)\n", encoding="utf-8")
                (d / "wiki" / "log.md").write_text(
                    f"# {ticker} log\n\n- {datetime.now(timezone.utc).isoformat()} init\n",
                    encoding="utf-8")
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"{ticker}: initialized at {d}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print the §10.1 freshness report as JSON; exit 1 if not initialized.

    Read-only, so it takes no lock (§7.1) — running `status` to watch a
    prefetch in flight is exactly the intended use.

    The `on_earnings` policy is driven by `last_earnings_date`, read from the
    stored calendar artifact. With no calendar on disk it returns None and the
    policy falls back to `EVENT_POLICY_FALLBACK_DAYS`, which errs in the safe
    direction: it can say "refetch" early, never "fresh" wrongly.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    try:
        state = load_state(d)
    except FileNotFoundError:
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    out = {
        "ticker": state["ticker"],
        # Reported under the registry kind that refreshes them: `prefetch
        # --kinds peers_candidates` is not a command anyone can run.
        "stale": sorted({STAGE_OF.get(k, k) for k in stale_kinds(
            state, datetime.now(timezone.utc),
            last_earnings=last_earnings_date(d), ticker_dir=d)}),
        "sections_dirty": state["report"].get("sections_dirty", []),
        "data": {k: {"current_ids": v["current_ids"], "fetched_at": v["fetched_at"]}
                 for k, v in state["data"].items()},
    }
    print(json.dumps(out, indent=2))
    return 0


def _parse_kinds(raw: str) -> list[str]:
    """Split `--kinds` into an ordered, deduped, trimmed list.

    Whitespace around each entry is stripped (`"prices, technical"` is two
    kinds, not one named `" technical"` that would fail the unknown-kind
    check), empty entries from trailing or doubled commas are dropped, and the
    first occurrence of a repeat wins so order is preserved.
    """
    return list(dict.fromkeys(k.strip() for k in raw.split(",") if k.strip()))


def _due_kinds(state: dict, wanted: list[str], now: datetime,
               ticker_dir: Path) -> set[str]:
    """Registry kinds a `--stale-only` run must refetch.

    A kind is due when its freshness policy says so — for a staged kind, when
    ANY of its stages is stale — or when none of its stages was ever recorded.
    """
    stale = {STAGE_OF.get(k, k)
             for k in stale_kinds(state, now,
                                  last_earnings=last_earnings_date(ticker_dir),
                                  ticker_dir=ticker_dir)}
    never = {k for k in wanted
             if not any(s in state["data"] for s in KIND_STAGES.get(k, (k,)))}
    return stale | never


def cmd_prefetch(args: argparse.Namespace) -> int:
    """Run the registered fetchers in dependency waves (§11.1).

    Exit 1 if the ticker is not initialized or a requested kind is unknown,
    2 if any fetcher failed, else 0. Mutating, so it takes the lock.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    try:
        state = load_state(d)
    except FileNotFoundError:
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    # The intersection (rather than DEFAULT_KINDS itself) keeps the default
    # honest when FETCHERS is swapped out, e.g. by the CLI tests.
    wanted = (_parse_kinds(args.kinds) if args.kinds
              else [k for k in FETCHERS if k in DEFAULT_KINDS])
    unknown = [k for k in wanted if k not in FETCHERS]
    if unknown:
        print(f"unknown kinds: {', '.join(unknown)} "
              f"(known: {', '.join(FETCHERS)})", file=sys.stderr)
        return 1

    if args.stale_only:
        due = _due_kinds(state, wanted, datetime.now(timezone.utc), d)
        run_kinds = [k for k in wanted if k in due]
    else:
        run_kinds = wanted

    # peers is the one fetcher taking an extra kwarg; it normalizes the raw
    # split (whitespace, case, dupes) itself, so the CLI passes it untouched.
    extra = ({"peers": {"user_peers": args.peers.split(",")}}
             if args.peers and "peers" in run_kinds else {})

    try:
        with TickerLock(d, "prefetch", force=args.force_lock):
            result = run_prefetch(ticker, d, state, run_kinds, FETCHERS,
                                  dependency_map(run_kinds), extra_kwargs=extra)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    result["skipped"] = [k for k in wanted if k not in run_kinds]
    print(json.dumps(result, indent=2))
    # warnings are informational; the exit code tracks `errors` only.
    return 0 if not result["errors"] else 2


# The four registry kinds `assemble._peer_row` reads. `technical` DEPENDS_ON
# `prices`, so prices is here to satisfy it rather than for its own artifact.
# Everything else — filings, transcripts, news, estimates — is subject-only
# work: the peer table compares four reported numbers, not a thesis.
PEER_METRIC_KINDS: list[str] = ["profile", "prices", "financials", "technical"]


def selected_peer_symbols(ticker_dir: Path) -> tuple[list[str], str | None]:
    """`(symbols, error)` for the selected comparables (§13.6).

    The subject itself appears in `peers_selected.json` flagged `is_subject`;
    it is dropped here because its own bronze is what `prefetch` gathered.
    """
    path = ticker_dir / "derived" / "peers" / "peers_selected.json"
    if not path.exists():
        return [], (f"no peers_selected.json at {path} "
                    f"(run: sra.py peers-select {ticker_dir.name})")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"cannot read peers_selected.json: {exc}"
    if not isinstance(payload, dict):
        return [], "peers_selected.json: top level must be an object"
    entries = (payload.get("data") or {}).get("peers")
    if not isinstance(entries, list):
        return [], "peers_selected.json: data.peers must be a list"
    symbols = [str(e.get("symbol") or "").strip().upper()
               for e in entries
               if isinstance(e, dict) and not e.get("is_subject")]
    return [s for s in symbols if s], None


def _ensure_peer_tree(data_root: Path, symbol: str) -> tuple[Path, dict] | None:
    """Create a peer's §4 tree if absent and return `(dir, state)`.

    None when the symbol is not a legal ticker — `valid_ticker` runs BEFORE the
    filesystem is touched, so a `../evil` entry in a model-written selection
    file is never interpolated into a path (§8.4 check 7).
    """
    if not valid_ticker(symbol):
        return None
    d = ticker_dir(data_root, symbol)
    for sub in TICKER_SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    try:
        state = load_state(d)
    except FileNotFoundError:
        init_state(d, symbol.upper())
        state = load_state(d)
    return d, state


def cmd_prefetch_peers(args: argparse.Namespace) -> int:
    """Fetch the peer-table metrics for each selected comparable (§13.6).

    Exit 1 when the subject is not initialized or has no selection, else 0. A
    peer that fails is a WARNING: four good comparables beat a build that
    refuses to finish because one provider was down.

    This must run AFTER peer selection. `prefetch --peers` feeds the candidate
    gather and cannot know the winners, which is why the peer table used to
    render every cell N/A.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    symbols, error = selected_peer_symbols(d)
    if error is not None:
        print(f"prefetch-peers: {error}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    out: dict = {"ticker": ticker, "peers": [], "skipped": [], "warnings": []}

    for symbol in symbols:
        prepared = _ensure_peer_tree(args.data_root, symbol)
        if prepared is None:
            out["warnings"].append(f"{symbol!r}: not a valid ticker; skipped")
            continue
        peer_dir, state = prepared

        if args.stale_only:
            due = _due_kinds(state, PEER_METRIC_KINDS, now, peer_dir)
            kinds = [k for k in PEER_METRIC_KINDS if k in due]
        else:
            kinds = list(PEER_METRIC_KINDS)
        if not kinds:
            out["skipped"].append(symbol)
            continue

        try:
            with TickerLock(peer_dir, "prefetch-peers", force=args.force_lock):
                result = run_prefetch(symbol, peer_dir, state, kinds, FETCHERS,
                                      dependency_map(kinds))
        except LockHeldError as exc:
            out["warnings"].append(f"{symbol}: {exc}")
            continue

        out["peers"].append(symbol)
        for kind, message in (result.get("errors") or {}).items():
            out["warnings"].append(f"{symbol}/{kind}: {message}")

    print(json.dumps(out, indent=2))
    return 0


def cmd_prefetch_macro(args: argparse.Namespace) -> int:
    """Gather shared macro evidence into `data/_MACRO/` (§12).

    §12.3: a failed macro series is a WARNING, not a failure. Macro data is
    context for every ticker, and one dead series must not block a build — so
    this exits 0 with the failures reported, unlike `prefetch`.
    """
    macro_dir = ticker_dir(args.data_root, MACRO_TICKER)
    if not (macro_dir / ".state.json").exists():
        print(f"{MACRO_TICKER}: not initialized (run: sra.py init {MACRO_TICKER})",
              file=sys.stderr)
        return 1

    known = list(FRED_SERIES) + list(MULTPL_SERIES)
    wanted = _parse_kinds(args.series) if args.series else known
    unknown = [s for s in wanted if s not in known]
    if unknown:
        print(f"unknown macro series: {', '.join(unknown)} "
              f"(known: {', '.join(known)})", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    state = load_state(macro_dir)
    fetched, skipped, errors, warnings = [], [], {}, {}

    try:
        with TickerLock(macro_dir, "prefetch-macro", force=args.force_lock):
            for series in wanted:
                key = (fred.artifact_id(series) if series in FRED_SERIES else series)
                if args.stale_only and key in state["data"] and key not in stale_kinds(
                        state, now, ticker_dir=macro_dir):
                    skipped.append(series)
                    continue
                try:
                    if series in FRED_SERIES:
                        ok, _paths, err = fred.fetch_fred_series(
                            FRED_SERIES[series], macro_dir, state, now=now)
                    else:
                        ok, _paths, err = multpl.fetch_multpl_series(
                            series, macro_dir, state, now=now)
                except Exception as exc:  # noqa: BLE001 — a fetcher bug is a warning too
                    ok, err = False, f"{series} crashed: {exc}"
                # State is committed after each series, as for prefetch (§7.1).
                save_state(macro_dir, state)
                if ok:
                    fetched.append(series)
                    if err:
                        warnings[series] = err
                else:
                    errors[series] = err
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"fetched": fetched, "skipped": skipped,
                      "errors": errors, "warnings": warnings}, indent=2))
    return 0  # §12.3: a failed macro series is a warning, never a build failure


def cmd_invalidate(args: argparse.Namespace) -> int:
    """Report (and with `--apply`, execute) what new evidence invalidates.

    Dry-run by default (§10.3): without `--apply` this reads the tree and prints
    the report, mutating nothing. That default is the point — invalidation
    reopens questions and dirties sections, and an operator should see the blast
    radius before paying for it.
    """
    resolved = _ledger_ticker(args)
    if resolved is None:
        return 1
    _ticker, d = resolved

    report = compute_invalidation(d, load_sections())
    payload = {"applied": bool(args.apply), **report.to_dict()}

    if args.apply and not report.is_empty():
        try:
            with TickerLock(d, "invalidate", force=args.force_lock):
                apply_invalidation(d, report)
        except LockHeldError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print(json.dumps(payload, indent=2))
    return 0


def _ledger_ticker(args: argparse.Namespace) -> tuple[str, Path] | None:
    """Resolve and require an initialized ticker for a ledger command."""
    resolved = _resolve_ticker(args)
    if resolved is None:
        return None
    ticker, d = resolved
    if not (d / ".state.json").exists():
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return None
    return ticker, d


def cmd_questions(args: argparse.Namespace) -> int:
    """Print the (optionally filtered) question ledger as JSON (§14)."""
    resolved = _ledger_ticker(args)
    if resolved is None:
        return 1
    _ticker, d = resolved

    rows = load_questions(d)
    if args.section:
        rows = [q for q in rows if q.get("section") == args.section]
    if args.status:
        rows = [q for q in rows if q.get("status") == args.status]
    print(json.dumps(rows, indent=2))
    return 0


def cmd_add_questions(args: argparse.Namespace) -> int:
    """Record questions against a section (§14.0, §14.1).

    This is the capture surface EVERY phase uses — a section writer that hits a
    gap, a critic, `sra-lint`, chart selection. Capture is cheap and idempotent
    (identity is `sha1(section|question)`), and is NEVER refused for volume: the
    reported open count is a backlog signal, not an error.
    """
    resolved = _ledger_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    if args.section not in SECTION_IDS:
        print(f"unknown section {args.section!r} (known: {', '.join(SECTION_IDS)})",
              file=sys.stderr)
        return 1

    texts = list(args.question or [])
    if args.from_file:
        path = Path(args.from_file)
        try:
            texts += [line.strip() for line in
                      path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 1
    if not texts:
        print("add-questions needs --question (repeatable) or --from-file",
              file=sys.stderr)
        return 1

    try:
        with TickerLock(d, "add-questions", force=args.force_lock):
            result = add_questions(d, args.section, texts,
                                   round_=args.round, origin=args.origin)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:      # §14's hash-collision refusal
        print(f"add-questions: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"ticker": ticker, "section": args.section, **result}, indent=2))
    return 0


def cmd_mark_answered(args: argparse.Namespace) -> int:
    """Close a question against stamped bronze evidence (§14.1).

    Exit 1 — leaving the question open — when the hash is unknown or any source
    is not bronze. §14.1 is explicit that a question with no citable evidence
    stays open; closing it anyway is the silent shortfall that rule forbids.
    """
    resolved = _ledger_ticker(args)
    if resolved is None:
        return 1
    _ticker, d = resolved

    sources = [s.strip() for part in (args.sources or [])
               for s in part.split(",") if s.strip()]
    artifacts = [a.strip() for part in (args.artifacts or [])
                 for a in part.split(",") if a.strip()]

    try:
        with TickerLock(d, "mark-answered", force=args.force_lock):
            row = mark_answered(d, args.question_hash, sources, artifacts=artifacts)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"mark-answered: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"mark-answered: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(row, indent=2))
    return 0


def cmd_record_attempt(args: argparse.Namespace) -> int:
    """Count dispatches that returned no citable evidence (§14.0).

    §20 defines `record_attempt` but §19's command table has no command reaching
    it; this subcommand is that addition, so the deferral floor is drivable from
    the CLI rather than only from Python.
    """
    resolved = _ledger_ticker(args)
    if resolved is None:
        return 1
    _ticker, d = resolved

    results: list[dict] = []
    try:
        with TickerLock(d, "record-attempt", force=args.force_lock):
            for qhash in args.question_hash:
                status = record_attempt(d, qhash)
                entry = next(q for q in load_questions(d) if q["hash"] == qhash)
                results.append({"hash": qhash, "attempts": entry["attempts"],
                                "status": status})
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"record-attempt: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
    return 0


def cmd_drop_question(args: argparse.Namespace) -> int:
    """Mark questions explicitly out of scope or unanswerable (§14.1).

    Same gap as `record-attempt`: §20 defines `drop_question` but §19's table
    has no command reaching it, and only a synthesizer may drop a question —
    which makes it model work whose bookkeeping still belongs to the driver
    (§3). Without this the synthesizer would have to hand-edit the ledger.

    `dropped` is always a decision. The ledger never infers it from silence.
    """
    resolved = _ledger_ticker(args)
    if resolved is None:
        return 1
    _ticker, d = resolved

    results: list[dict] = []
    try:
        with TickerLock(d, "drop-question", force=args.force_lock):
            for qhash in args.question_hash:
                entry = drop_question(d, qhash)
                results.append({"hash": qhash, "status": entry["status"]})
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"drop-question: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
    return 0


def cmd_peers_candidates(args: argparse.Namespace) -> int:
    """Gather the four peer sources into `derived/peers/` (§13.3).

    Exit 1 if the ticker is not initialized, 2 if every source failed AND the
    user named nobody (§13.5) — a single dead source is a warning, since the
    remaining ones still produce a usable table.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    try:
        state = load_state(d)
    except FileNotFoundError:
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    # Split only; `fetch_peers` normalizes case, whitespace and duplicates
    # itself, so the CLI does not get a second opinion about what a symbol is.
    user = [s for s in args.peers.split(",") if s.strip()] if args.peers else None

    try:
        with TickerLock(d, "peers-candidates", force=args.force_lock):
            ok, paths, err = fetch_peers(ticker, d, state, user_peers=user,
                                         top_funds=args.top_funds)
            save_state(d, state)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"ok": ok, "written": [str(p) for p in paths],
                      "warnings": err}, indent=2))
    return 0 if ok else 2


def _parse_stamp(value: object) -> datetime | None:
    """An ISO timestamp off disk, as an aware datetime, or None."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read_peers_json(path: Path) -> tuple[StructuredMeta | None, object, str | None]:
    """`(meta, data, error)` for a shaped peers artifact; error is None on success.

    `peers_ranked.json` is on the MODEL's write path and every one of these can
    be caught by a run interrupted mid-write, so a malformed file is a
    foreseeable input: `main()` owes the caller an exit code, not a
    JSONDecodeError traceback.
    """
    try:
        meta, data = read_structured(path)
    except json.JSONDecodeError as exc:
        return None, None, f"invalid JSON in {path}: {exc}"
    except (KeyError, TypeError, ValueError) as exc:
        return None, None, f"malformed artifact {path}: {type(exc).__name__}: {exc}"
    return meta, data, None


def cmd_peers_select(args: argparse.Namespace) -> int:
    """Pin the user's peers, fill the rest from the ranking, write five (§13.3).

    Deterministic: nothing is scored here. The model returns an ordered list and
    this applies it under the §13.4 top-up contract.

    Exit 1 when there is no candidate table, an input is malformed, the ranking
    predates the candidate set (§13.5 — it ranked an older table), or the pinned
    list is short and there is no ranking to top up with.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    try:
        state = load_state(d)
    except FileNotFoundError:
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    cand_path = peers_path(d, "peers_candidates")
    if not cand_path.exists():
        print(f"peers-select: no peers_candidates.json for {ticker} "
              f"(run: sra.py peers-candidates {ticker})", file=sys.stderr)
        return 1
    cand_meta, cand_data, err = _read_peers_json(cand_path)
    if err or cand_meta is None:
        print(f"peers-select: {err}", file=sys.stderr)
        return 1
    if not isinstance(cand_data, dict) or not isinstance(
            cand_data.get("candidates"), list):
        print(f"peers-select: malformed {cand_path}: data.candidates must be a list",
              file=sys.stderr)
        return 1
    candidates = cand_data["candidates"]

    # The freshness key is when the candidate SET last changed, not when the
    # file was last written: `peers` is a default prefetch kind, so a routine
    # refresh rewrites the table without changing anything a ranking depends on.
    changed_at = (_parse_stamp(cand_data.get("candidates_changed_at"))
                  or _parse_stamp(cand_meta.computed_at)
                  or datetime.min.replace(tzinfo=timezone.utc))

    derived: list[str] = ["peers_candidates"]
    warnings: list[str] = []

    # --- pinned peers (§13.5's stale-file rule) -----------------------------
    pinned, recorded_at = read_user_peers(d)
    state_peers = [str(s).strip().upper() for s in
                   (state.get("derived", {}).get("peers_selected", {})
                    .get("user_peers") or [])]
    if pinned:
        stamp = _parse_stamp(recorded_at)
        if stamp is None or stamp < changed_at:
            # Left by an earlier, different run — pinning it would fill slots
            # with peers the user never named THIS time. Nothing is deleted;
            # §13.5 says re-assert with `peers-candidates --peers`.
            msg = (f"ignoring stale peers_user.json (recorded_at {recorded_at} "
                   f"predates the current candidate set of {changed_at.isoformat()})"
                   + (f"; falling back to {len(state_peers)} peers persisted in state"
                      if state_peers else ""))
            print(f"peers-select: {msg}", file=sys.stderr)
            warnings.append(msg)
            pinned = state_peers
        else:
            derived.append("peers_user")
    elif state_peers:
        pinned = state_peers

    # --- the ranking --------------------------------------------------------
    ranked: list = []
    ranked_path = Path(args.ranked_file) if args.ranked_file else \
        peers_path(d, "peers_ranked")

    if len(pinned) >= PEER_SET_SIZE:
        # Every slot is spoken for, so §13.4 skipped the rater and there is no
        # ranking to read at all. `apply_selection` seats the first PEER_SET_SIZE
        # and returns the extras as runners-up.
        origin = "user_provided"
    elif ranked_path.exists():
        ranked_meta, ranked_data, err = _read_peers_json(ranked_path)
        if err or ranked_meta is None:
            print(f"peers-select: {err}", file=sys.stderr)
            return 1
        if not isinstance(ranked_data, list):
            print(f"peers-select: {ranked_path} must carry a JSON list of ranked "
                  f"peers under `data`, got {type(ranked_data).__name__}",
                  file=sys.stderr)
            return 1
        generated_at = _parse_stamp(ranked_meta.generated_at)
        if generated_at is None:
            print(f"peers-select: {ranked_path.name} has no readable "
                  f"_meta.generated_at, so its ranking cannot be checked against "
                  f"the candidate set (§13.5) — re-run /sra-peers", file=sys.stderr)
            return 1
        if generated_at < changed_at:
            print(f"peers-select: {ranked_path.name} ({generated_at.isoformat()}) "
                  f"predates the current candidate set ({changed_at.isoformat()}) "
                  f"— it ranked an older table; re-run /sra-peers to rank the "
                  f"current one", file=sys.stderr)
            return 1
        ranked = ranked_data
        origin = "user_topped_up" if pinned else "model_rated"
        if ranked_path == peers_path(d, "peers_ranked"):
            derived.append("peers_ranked")
    else:
        print(f"peers-select: {len(pinned)} user peers is under {PEER_SET_SIZE} "
              f"and {ranked_path.name} does not exist — run /sra-peers to rank "
              f"first", file=sys.stderr)
        return 1

    selected, runners = apply_selection(candidates, ranked, pinned=pinned)
    if len(selected) < PEER_SET_SIZE:
        # The whole premise is five comparables; a short set reporting success
        # is the same silent-shortfall shape as a source that vanishes.
        msg = (f"only {len(selected)} of {PEER_SET_SIZE} peers selected "
               f"({len(pinned)} pinned, {len(ranked)} ranked)")
        print(f"peers-select: {msg}", file=sys.stderr)
        warnings.append(msg)
    for row in runners:
        if row["symbol"] in pinned:
            row["origin"] = "user_provided"   # §13.4: extras stay attributed

    now = _utcnow()
    # `model` shape (§6.2, §13.3): this records model-mediated judgment, so no
    # `url` and no `fetch_cmd`. `source` names where the judgment came from —
    # the rater, or the user when their own list filled every slot.
    meta = StructuredMeta(
        id="peers_selected", ticker=ticker, producer="model",
        title=f"{ticker} selected peer set",
        source="sra-rater" if "peers_ranked" in derived else "user_provided",
        generated_at=now.isoformat(), as_of=now.date().isoformat(),
        derived_from=derived)

    try:
        with TickerLock(d, "peers-select", force=args.force_lock):
            write_derived(d, meta, {"peers": selected, "runners_up": runners,
                                    "origin": origin, "warnings": warnings},
                          namespace="peers")
            # `peers` in data{} is what `prefetch --stale-only` reads (§13.3:
            # the gather stamps `peers_candidates`, selection stamps `peers`).
            record_fetch(state, "peers", "peers_selected", now, {"policy_days": 90})
            # ...and the silver lineage entry (§7). `record_derived` REPLACES
            # derived[key], so the persisted user list — which §13.5 makes the
            # fallback above — is captured first and restored after.
            carried = {k: v for k, v in
                       state.get("derived", {}).get("peers_selected", {}).items()
                       if k in ("user_peers", "asked_at")}
            record_derived(state, "peers_selected", "peers_selected", now,
                           _peer_refs(d, derived))
            state["derived"]["peers_selected"].update(carried)
            save_state(d, state)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"selected": [r["symbol"] for r in selected],
                      "origin": origin,
                      "runners_up": [r["symbol"] for r in runners],
                      "warnings": warnings}, indent=2))
    return 0


def _peer_refs(ticker_dir: Path, ids: list[str]) -> list[dict]:
    """Stamped `derived_from` references for `record_derived` (§7).

    The stamp is what lets `invalidate` (§10.2) notice an input was rewritten,
    so each id carries its artifact's own timestamp. `peers_user` is bare by
    design, so its `recorded_at` stands in.
    """
    refs: list[dict] = []
    for artifact_id in ids:
        if artifact_id == "peers_user":
            _peers, recorded_at = read_user_peers(ticker_dir)
            refs.append({"id": artifact_id,
                         "fetched_at": recorded_at or _utcnow().isoformat()})
            continue
        try:
            meta, _ = read_structured(peers_path(ticker_dir, artifact_id))
        except (OSError, ValueError, json.JSONDecodeError):
            refs.append({"id": artifact_id, "fetched_at": _utcnow().isoformat()})
            continue
        stamp = meta.computed_at or meta.generated_at or meta.fetched_at
        key = ("computed_at" if meta.computed_at else
               "generated_at" if meta.generated_at else "fetched_at")
        refs.append({"id": artifact_id, key: stamp or _utcnow().isoformat()})
    return refs


def _utcnow() -> datetime:
    """Wall clock, in one place so `fetch-urls` freshness is injectable in tests."""
    return datetime.now(timezone.utc)


def cmd_fetch_urls(args: argparse.Namespace) -> int:
    """Harvest researcher/aggregator `cited_urls` into bronze (§8.3).

    Without `--from`, every answer and aggregator source with an unharvested
    URL is processed. With it, exactly that document is processed — and its
    previously failed URLs are re-attempted, which the bulk path deliberately
    does not do.

    §8.3 makes a failed TARGET fetch a warning, not a command failure: the URL
    gets a `null` in the map (telling the synthesizer the claim is not citable)
    and the command still exits 0. Exit 1 is reserved for the cases where there
    is nothing to work from at all — an uninitialized ticker, or a `--from` id
    that names no readable document.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    if not (d / ".state.json").exists():
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    if args.source:
        try:
            _reject_path_traversal(args.source, "--from")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        answer = d / "derived" / "answers" / f"{args.source}.md"
        path = answer if answer.exists() else resolve_source(d, args.source)
        if path is None:
            print(f"{ticker}: no answer or source with id {args.source!r}",
                  file=sys.stderr)
            return 1
        targets = [path]
    else:
        targets = harvest_targets(d)

    now = _utcnow()
    fetched: list[str] = []
    skipped: list[str] = []
    errors: dict[str, str] = {}

    try:
        with TickerLock(d, "fetch-urls", force=args.force_lock):
            for target in targets:
                try:
                    result = harvest_answer(d, target, args.max, now=now)
                except (KeyError, ValueError, OSError) as exc:
                    # §8.3's one fatal condition: the answer file itself is
                    # unreadable. Only reachable with --from, since the bulk
                    # path already skipped anything it could not read.
                    print(f"cannot read {target}: {type(exc).__name__}: {exc}",
                          file=sys.stderr)
                    return 1
                fetched += result["fetched"]
                skipped += result["skipped"]
                errors.update(result["errors"])
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for url, reason in errors.items():
        print(f"warning: {url}: {reason}", file=sys.stderr)
    print(json.dumps({"fetched": fetched, "skipped": skipped, "errors": errors},
                     indent=2))
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    """Regenerate `sources/00_manifest.md` and print its path (§5.1, §9).

    Mutating (it writes into `sources/`), so it takes the lock — a manifest
    built while a prefetch is mid-write would catalog a half-written tree.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    if not (d / ".state.json").exists():
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    try:
        with TickerLock(d, "manifest", force=args.force_lock):
            path = build_manifest(d)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(path)
    return 0


def cmd_grep(args: argparse.Namespace) -> int:
    """Search bronze bodies and print ranked hits as JSON (§9).

    Read-only, so no lock: grepping while a prefetch runs is normal.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    if not (d / ".state.json").exists():
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    try:
        hits = grep(
            d,
            args.pattern,
            kinds=[k.strip() for k in args.kinds.split(",") if k.strip()]
            if args.kinds else None,
            context=args.context,
            top_k=args.top_k,
            include_archived=args.include_archived,
        )
    except ValueError as exc:
        # A malformed regex must not read as "no such evidence exists".
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps([asdict(h) for h in hits], indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print one artifact whole (§9): markdown as-is, JSON pretty-printed.

    Read-only, so no lock. Exit 1 when the id resolves nowhere.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    try:
        # The id is interpolated into a path, so a separator or `..` has to be
        # refused here — containment is structural (§8.4 check 7), not something
        # to detect after the fact.
        _reject_path_traversal(args.id, "id")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not (d / ".state.json").exists():
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    path = resolve_artifact(d, args.id)
    if path is None:
        print(f"{ticker}: no artifact with id {args.id!r} in sources/, "
              f"sources/archive/, structured/ or derived/", file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            print(json.dumps(json.loads(text), indent=2))
            return 0
        except ValueError:
            # A hand-mangled artifact should still be readable — you cannot fix
            # what `show` refuses to display. `validate` (§8.4) makes it fatal.
            pass
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Run the fatal validation gate and print findings as JSON (§8.4).

    Exit 1 on any error-severity finding. There is deliberately no `--force`:
    §8.4 states the gate has none, and one you can wave through is not a gate.
    Read-only, so no lock.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved

    if not (d / ".state.json").exists():
        print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
        return 1

    findings = validate(d, args.data_root)
    print(json.dumps([asdict(f) for f in findings], indent=2))
    return 1 if has_errors(findings) else 0


def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    """Replay answered questions through the grep path and report recall (§9.2).

    Read-only, so no lock. Exit 1 only for a `--baseline` regression: the
    metric is a regression test, and a low absolute number on a young ledger
    is information, not a failure.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    result = evaluate(d, k=args.k)
    if args.baseline:
        passed, message = compare_to_baseline(result, Path(args.baseline))
        result["baseline"] = {"path": args.baseline, "passed": passed,
                              "message": message}
        print(json.dumps(result, indent=2))
        if not passed:
            print(f"eval-retrieval: {message}", file=sys.stderr)
            return 1
        return 0

    print(json.dumps(result, indent=2))
    return 0


def _require_initialized(ticker: str, ticker_dir: Path) -> bool:
    if (ticker_dir / ".state.json").exists():
        return True
    print(f"{ticker}: not initialized (run: sra.py init {ticker})", file=sys.stderr)
    return False


def cmd_wiki_log(args: argparse.Namespace) -> int:
    """Append one timestamped entry to `wiki/log.md` (§4)."""
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1
    try:
        with TickerLock(d, "wiki-log", force=args.force_lock):
            path = append_log(d, args.entry, agents=args.agents,
                              tokens=args.tokens, minutes=args.minutes,
                              run=args.run)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_charts(args: argparse.Namespace) -> int:
    """Render chart candidates into `charts/candidates/` (§16.1, §16.4).

    Two passes, split by `requires_verdict`. The default pass runs the exhibits
    that are pure functions of bronze; `--verdict` runs the conclusion-dependent
    ones after the polish chain has written `verdict.json`.

    Three outcomes per renderer, and they are deliberately different things:
    rendered, SKIPPED (returned `None` — its inputs are not on disk, which §16.1
    calls normal degradation), and errored. One broken renderer must not cost
    the other exhibits, so a raising renderer is caught, recorded, and the walk
    continues; the exit code is 2 if anything errored.

    Refusing `--verdict` without a verdict (exit 1) is the one hard stop: those
    renderers READ the fair value, and running them without one would quietly
    produce a football field with nothing plotted on it.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    if args.verdict and load_verdict(d) is None:
        print(f"{ticker}: no verdict.json under reports/ — run the polish chain "
              f"before `charts {ticker} --verdict` (§16.4)", file=sys.stderr)
        return 1

    rendered: list[str] = []
    skipped: list[str] = []
    errors: dict[str, str] = {}

    try:
        with TickerLock(d, "charts", force=args.force_lock):
            for name in select(RENDERERS, verdict=args.verdict):
                try:
                    result = RENDERERS[name].fn(d)
                except Exception as exc:  # one bad renderer, not a lost pass
                    errors[name] = f"{type(exc).__name__}: {exc}"
                    continue
                (rendered if result is not None else skipped).append(name)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for name, message in errors.items():
        print(f"warning: {name}: {message}", file=sys.stderr)
    print(json.dumps({"rendered": rendered, "skipped": skipped,
                      "errors": errors}, indent=2))
    return 0 if not errors else 2


def cmd_assemble(args: argparse.Namespace) -> int:
    """Assemble one report run into markdown, HTML and PDF (§15.3).

    Deterministic and agent-free by construction: everything it needs was
    written by the write wave, the polish chain and the chart-selection skill
    before it ran (§16.4's ordering).

    Exit 1 on any contract failure — a missing draft, a chartbook naming an
    exhibit that did not render, an internal filename in prose, a citation that
    resolves to nothing or to silver. A render failure (no pandoc, no Pango) is
    NOT exit 1: §22.3 degrades it into a reported error, since the assembled
    markdown and its references are already on disk.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    run_dir = resolve_run(d, args.run, _utcnow().date())
    if not run_dir.is_dir():
        print(f"{ticker}: no report run at {run_dir} — run the write wave first",
              file=sys.stderr)
        return 1

    try:
        with TickerLock(d, "assemble", force=args.force_lock):
            ok, data, err = assemble(d, run_dir)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not ok:
        print(f"assemble: {err}", file=sys.stderr)
        return 1

    for message in data["render_errors"]:
        print(f"warning: {message}", file=sys.stderr)
    print(json.dumps({
        "run": run_dir.name,
        "markdown": str(data["markdown"]),
        "html": str(data["html"]) if data["html"] else None,
        "pdf": str(data["pdf"]) if data["pdf"] else None,
        "citations": data["citations"],
        "exhibits": data["exhibits"],
        "render_errors": data["render_errors"],
    }, indent=2))
    return 0


def cmd_run_log(args: argparse.Namespace) -> int:
    """Assemble `reports/<run>/run_log.md` from the run's task logs (§23.4).

    Read-mostly and deterministic: it writes one generated file from inputs
    that already exist, so it takes no lock and can be run against a build
    that is still in flight — which is when an audit log is most wanted.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    run_dir = resolve_run(d, args.run, _utcnow().date())
    if not run_dir.is_dir():
        print(f"{ticker}: no run directory {run_dir.name} under reports/",
              file=sys.stderr)
        return 1
    print(build_run_log(d, run_dir))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Stamp a report run immutable and point `reports/latest` at it (§15.3).

    The stamp is what makes the run immutable in practice: `current_run` (§15.3)
    refuses to hand a stamped directory back to the next build, so a second
    same-day build writes `<date>_2` and §24's "diff against the first snapshot
    must remain possible" holds.

    Exit 1 when there is nothing to stamp (no run, or a run with no assembled
    report) or when the run is already stamped.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1

    now = _utcnow()
    run_dir = resolve_run(d, args.run, now.date())
    existing = run_dirs(d)
    if not run_dir.is_dir():
        # `current_run` has already stepped past a stamped run, so the useful
        # message here is about the run the caller means, not the empty name it
        # resolved to.
        if args.run is None and existing and is_snapshotted(existing[-1]):
            print(f"{ticker}: {existing[-1].name} is already snapshotted — "
                  f"assemble a new run before snapshotting again (§15.3)",
                  file=sys.stderr)
        else:
            print(f"{ticker}: no report run to snapshot at {run_dir} "
                  f"(run: sra.py assemble {ticker})", file=sys.stderr)
        return 1
    if is_snapshotted(run_dir):
        print(f"{ticker}: {run_dir.name} is already snapshotted — assemble a new "
              f"run before snapshotting again (§15.3)", file=sys.stderr)
        return 1
    if not (run_dir / "report.md").exists():
        print(f"{ticker}: {run_dir.name} has no report.md "
              f"(run: sra.py assemble {ticker})", file=sys.stderr)
        return 1

    try:
        with TickerLock(d, "snapshot", force=args.force_lock):
            state = load_state(d)
            consumed = list(state.get("report", {}).get("sections_dirty") or [])
            stamp = write_snapshot(run_dir, now, sections_consumed=consumed)
            link = link_latest(d, run_dir)
            state["report"]["last_generated"] = now.isoformat()
            # §7: the dirty list accumulates until regeneration consumes it —
            # and this is the moment it was consumed, not `assemble`, since an
            # assembly that is never snapshotted produced no report anyone reads.
            state["report"]["sections_dirty"] = []
            save_state(d, state)
            append_log(d, f"snapshot: {run_dir.name}"
                          + (f" (sections regenerated: {', '.join(consumed)})"
                             if consumed else ""))
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"run": run_dir.name, "path": str(run_dir),
                      "latest": str(link), "snapshotted_at": stamp["snapshotted_at"],
                      "sections_regenerated": consumed}, indent=2))
    return 0


def cmd_wiki_index(args: argparse.Namespace) -> int:
    """Regenerate `wiki/00_index.md` as the wiki's navigation page (§14.2)."""
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1
    try:
        with TickerLock(d, "wiki-index", force=args.force_lock):
            path = update_index(d, load_sections())
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_mark_dirty(args: argparse.Namespace) -> int:
    """Record a report section as needing regeneration (§7).

    The section name is checked against `sections.yaml`: a typo would
    otherwise create a dirty flag that nothing ever consumes, so the section
    would silently never be rebuilt.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if args.section not in SECTION_IDS:
        print(f"unknown section {args.section!r}: expected one of "
              f"{', '.join(SECTION_IDS)}", file=sys.stderr)
        return 1
    if not _require_initialized(ticker, d):
        return 1
    try:
        with TickerLock(d, "mark-dirty", force=args.force_lock):
            state = load_state(d)
            mark_section_dirty(state, args.section)
            save_state(d, state)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{ticker}: marked {args.section} dirty")
    return 0


def cmd_wiki_lint(args: argparse.Namespace) -> int:
    """Run the ADVISORY wiki lint and print findings as JSON (§22.1).

    Always exits 0, even with findings: an advisory check that fails the build
    is a fatal check nobody agreed to. `validate` (§8.4) is the gate that
    fails. Read-only, so no lock.
    """
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1
    print(json.dumps([asdict(f) for f in wiki_lint(d, load_sections())], indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser.

    `--data-root` lives on each subparser rather than the top-level parser so
    it may follow the subcommand (`sra.py init PANW --data-root /tmp/x`), which
    is how every skill and test invokes it.
    """
    p = argparse.ArgumentParser(prog="sra.py", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, fn, *, mutating: bool) -> argparse.ArgumentParser:
        sp = sub.add_parser(name)
        sp.add_argument("ticker")
        sp.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                        help="root of the per-ticker data tree (default: <repo>/data)")
        if mutating:
            sp.add_argument("--force-lock", action="store_true",
                            help="break a ticker lock older than 6h and proceed")
        sp.set_defaults(fn=fn, force_lock=False)
        return sp

    add("init", cmd_init, mutating=True)
    add("status", cmd_status, mutating=False)
    add("manifest", cmd_manifest, mutating=True)

    # prefetch-macro takes no ticker: it always targets the shared _MACRO tree.
    sp = sub.add_parser("prefetch-macro")
    sp.add_argument("--series", default=None,
                    help=f"comma-separated macro series (default: all; known: "
                         f"{', '.join(list(FRED_SERIES) + list(MULTPL_SERIES))})")
    sp.add_argument("--stale-only", action="store_true",
                    help="only fetch series that are stale or never fetched")
    sp.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                    help="root of the per-ticker data tree (default: <repo>/data)")
    sp.add_argument("--force-lock", action="store_true",
                    help="break a lock older than 6h and proceed")
    sp.set_defaults(fn=cmd_prefetch_macro, force_lock=False)

    sp = add("prefetch", cmd_prefetch, mutating=True)
    sp.add_argument("--kinds", default=None,
                    help=f"comma-separated data kinds (default: "
                         f"{', '.join(DEFAULT_KINDS)})")
    sp.add_argument("--stale-only", action="store_true",
                    help="only fetch kinds that are stale or never fetched")
    sp.add_argument("--peers", default=None,
                    help="comma-separated user-provided peer list (peers fetcher only)")

    sp = add("prefetch-peers", cmd_prefetch_peers, mutating=True)
    sp.add_argument("--stale-only", action="store_true",
                    help="only fetch peers whose metric artifacts are stale "
                         "or were never fetched")

    sp = add("invalidate", cmd_invalidate, mutating=True)
    sp.add_argument("--apply", action="store_true",
                    help="execute the transitions (default: dry-run, §10.3)")

    sp = add("questions", cmd_questions, mutating=False)
    sp.add_argument("--section", default=None,
                    help=f"restrict to one section ({', '.join(SECTION_IDS)})")
    sp.add_argument("--status", default=None,
                    help=f"restrict to one status ({', '.join(STATUSES)})")

    sp = add("add-questions", cmd_add_questions, mutating=True)
    sp.add_argument("--section", required=True,
                    help=f"the section these questions belong to "
                         f"({', '.join(SECTION_IDS)})")
    sp.add_argument("--question", action="append", default=None,
                    help="a question (repeatable; each occurrence is one entry)")
    sp.add_argument("--from-file", default=None,
                    help="read questions from a file, one per line")
    sp.add_argument("--round", type=int, default=1,
                    help="the research round these questions belong to")
    sp.add_argument("--origin", default=DEFAULT_ORIGIN,
                    help="who raised them (§23.4 purposes, plus seed and user)")

    sp = add("mark-answered", cmd_mark_answered, mutating=True)
    sp.add_argument("--question-hash", required=True, help="the question's id")
    sp.add_argument("--sources", action="append", default=None,
                    help="bronze ids supporting the answer (repeatable or comma-separated)")
    sp.add_argument("--artifacts", action="append", default=None,
                    help="researcher-answer ids, for audit only (never evidence)")

    sp = add("record-attempt", cmd_record_attempt, mutating=True)
    sp.add_argument("--question-hash", action="append", required=True,
                    help="question id whose dispatch returned no citable evidence")

    sp = add("drop-question", cmd_drop_question, mutating=True)
    sp.add_argument("--question-hash", action="append", required=True,
                    help="question id the synthesizer ruled out of scope "
                         "or unanswerable (repeatable)")

    sp = add("peers-candidates", cmd_peers_candidates, mutating=True)
    sp.add_argument("--peers", default=None,
                    help="comma-separated user-provided peer list to pin")
    sp.add_argument("--top-funds", type=int, default=None,
                    help="how many overlapping funds to use (default: 5)")

    sp = add("peers-select", cmd_peers_select, mutating=True)
    sp.add_argument("--ranked-file", default=None,
                    help="path to the rater's ranking (default: "
                         "derived/peers/peers_ranked.json)")

    sp = add("fetch-urls", cmd_fetch_urls, mutating=True)
    sp.add_argument("--from", dest="source", default=None,
                    help="harvest only this answer or aggregator source id "
                         "(default: every document with unharvested cited_urls)")
    sp.add_argument("--max", type=int, default=None,
                    help="cap the number of URLs fetched per document")

    add("validate", cmd_validate, mutating=False)

    sp = add("eval-retrieval", cmd_eval_retrieval, mutating=False)
    sp.add_argument("--k", type=int, default=DEFAULT_K,
                    help=f"how many grep hits count as retrieved (default: {DEFAULT_K})")
    sp.add_argument("--baseline", default=None,
                    help="recorded baseline JSON; exit 1 if mean recall drops "
                         "more than 0.02 below it (§9.2)")

    sp = add("charts", cmd_charts, mutating=True)
    sp.add_argument("--verdict", action="store_true",
                    help="render the conclusion-dependent exhibits instead, "
                         "reading reports/latest/verdict.json (§16.4)")

    sp = add("assemble", cmd_assemble, mutating=True)
    sp.add_argument("--run", default=None,
                    help="run directory name under reports/ (default: the "
                         "newest run that has not been snapshotted)")

    sp = add("snapshot", cmd_snapshot, mutating=True)
    sp.add_argument("--run", default=None,
                    help="run directory name under reports/ (default: the "
                         "newest run that has not been snapshotted)")

    add("wiki-index", cmd_wiki_index, mutating=True)
    add("wiki-lint", cmd_wiki_lint, mutating=False)

    sp = add("run-log", cmd_run_log, mutating=False)
    sp.add_argument("--run", default=None,
                    help="run directory name under reports/ (default: the "
                         "newest run that has not been snapshotted)")

    sp = add("wiki-log", cmd_wiki_log, mutating=True)
    sp.add_argument("--entry", required=True, help="log line to append")
    # §23.4: the journal stays one entry per phase boundary. These only add
    # what that entry cost and where the detail lives.
    sp.add_argument("--agents", type=int, default=None,
                    help="how many model subagents the phase used")
    sp.add_argument("--tokens", type=int, default=None,
                    help="total tokens the phase consumed")
    sp.add_argument("--minutes", type=float, default=None,
                    help="wall clock the phase took")
    sp.add_argument("--run", default=None,
                    help="run directory whose run_log.md the entry links to")

    sp = add("mark-dirty", cmd_mark_dirty, mutating=True)
    sp.add_argument("--section", required=True,
                    help=f"report section to mark dirty ({', '.join(SECTION_IDS)})")

    sp = add("show", cmd_show, mutating=False)
    sp.add_argument("id", help="artifact id (source, structured, or derived)")

    sp = add("grep", cmd_grep, mutating=False)
    sp.add_argument("pattern",
                    help="whitespace-separated terms, each a case-insensitive regex")
    sp.add_argument("--kinds", default=None,
                    help="comma-separated source kinds to restrict the search to")
    sp.add_argument("--context", type=int, default=2,
                    help="lines of context on each side of the match (default: 2)")
    sp.add_argument("--top-k", type=int, default=None,
                    help="keep only the top K hits after ranking")
    sp.add_argument("--include-archived", action="store_true",
                    help="also search sources/archive/ (§5)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
