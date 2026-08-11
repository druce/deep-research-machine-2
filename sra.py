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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from lib.lock import LockHeldError, TickerLock
from lib.statefile import init_state, load_state, stale_kinds

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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
