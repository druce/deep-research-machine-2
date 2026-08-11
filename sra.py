#!/usr/bin/env python3
"""sra6 driver CLI — all deterministic pipeline work lives behind this entry point.

Nothing deterministic belongs in a skill (§3): if it can be a function, it is a
subcommand here. Skills orchestrate model work and call this CLI for everything
mechanical.

See sra6-spec.md §19 for the target command surface and §20 for module
contracts. This module currently implements the skeleton: `init` and `status`.
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

from lib.fetchers.registry import (
    DEFAULT_KINDS,
    FETCHERS,
    KIND_STAGES,
    STAGE_OF,
    dependency_map,
    run_prefetch,
)
from lib.grep import grep
from lib.lock import LockHeldError, TickerLock
from lib.manifest import build_manifest
# `_reject_path_traversal` is imported rather than reimplemented so the rule for
# what counts as a bare artifact id (§8.4) has exactly one definition.
from lib.provenance import _reject_path_traversal, resolve_artifact
from lib.sections import SECTION_IDS, load_sections
from lib.statefile import init_state, load_state, mark_section_dirty, save_state, stale_kinds
from lib.validate import has_errors, validate
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

    `last_earnings` is None until `lib.fetchers.calendar.last_earnings_date`
    exists (Phase 5); until then an `on_earnings` kind ages on
    `EVENT_POLICY_FALLBACK_DAYS`, which is conservative in the safe direction
    (it can say "refetch" early, never "fresh" wrongly).
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
        "stale": sorted(stale_kinds(state, datetime.now(timezone.utc),
                                    last_earnings=None, ticker_dir=d)),
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
             for k in stale_kinds(state, now, last_earnings=None,
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
            path = append_log(d, args.entry)
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(path)
    return 0


def cmd_wiki_index(args: argparse.Namespace) -> int:
    """Regenerate `wiki/00_index.md` from page frontmatter (§4)."""
    resolved = _resolve_ticker(args)
    if resolved is None:
        return 1
    ticker, d = resolved
    if not _require_initialized(ticker, d):
        return 1
    try:
        with TickerLock(d, "wiki-index", force=args.force_lock):
            path = update_index(d)
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

    sp = add("prefetch", cmd_prefetch, mutating=True)
    sp.add_argument("--kinds", default=None,
                    help=f"comma-separated data kinds (default: "
                         f"{', '.join(DEFAULT_KINDS)})")
    sp.add_argument("--stale-only", action="store_true",
                    help="only fetch kinds that are stale or never fetched")
    sp.add_argument("--peers", default=None,
                    help="comma-separated user-provided peer list (peers fetcher only)")

    add("validate", cmd_validate, mutating=False)
    add("wiki-index", cmd_wiki_index, mutating=True)
    add("wiki-lint", cmd_wiki_lint, mutating=False)

    sp = add("wiki-log", cmd_wiki_log, mutating=True)
    sp.add_argument("--entry", required=True, help="log line to append")

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
