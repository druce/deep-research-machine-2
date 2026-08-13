#!/usr/bin/env python3
"""Ownership, insider activity and short interest, via OpenBB (spec §11.1).

The one real gap in sra6's deterministic gather. Everything else a section
needs — statements, prices, estimates, targets, filings, transcripts, peers —
has a fetcher; who owns the shares, who is selling them, and how heavily the
stock is shorted did not, so the topic briefs told agents to go and web-search
for it. `prompts/prefetch_research/executives.md` literally asks for "the
pattern only, from Form 4 aggregates", which is a deterministic query dressed up
as research. Three sections lean on this data — executives (insider selling),
thesis (short interest as the bear case's temperature) and risks — and paying
model tokens to approximate it from news articles was the wrong trade.

Why OpenBB, and why out of process
----------------------------------
OpenBB normalizes ~50 providers behind one schema, so `institutional`,
`insider_trading` and `short_interest` arrive shaped the same way whichever
upstream answered. That is worth a lot here: FINRA's short-interest file and
FMP's Form 4 feed have nothing in common structurally.

It is NOT a dependency of this project, and deliberately so — `pyproject.toml`
records that sra5's OpenBB dependency was dropped for weight, and it is 51
packages. So this fetcher **shells out to a Python interpreter that has OpenBB
installed** and reads JSON back. That keeps sra6's environment small, avoids
pinning this project to OpenBB's transitive tree, and sidesteps the fact that
the available install runs on a different Python minor version than sra6
requires.

Resolution order for that interpreter is `OPENBB_PYTHON`, then this project's
own interpreter if `openbb` happens to be importable there. Absent both, the
kind degrades: a warning, no artifact, and the build continues. Ownership data
is context, never §11.1 minimum-viable input.

Credentials travel in the subprocess ENVIRONMENT, never in argv, because argv is
world-readable in `ps` output on a shared machine.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from lib.provenance import StructuredMeta, write_structured
from lib.statefile import record_fetch

DEPENDS_ON: tuple[str, ...] = ()

# Insider filings arrive continuously and FINRA settles short interest twice a
# month, so a week keeps both usefully current without re-querying every build.
OWNERSHIP_POLICY_DAYS = 7

SUBPROCESS_TIMEOUT = 180

# Rows kept per dataset. Insider trades are the long tail — a year of Form 4s on
# an active issuer runs to hundreds, and a section cites the PATTERN, not every
# transaction. Short interest is a time series, so it keeps more.
MAX_INSIDER_ROWS = 60
MAX_SHORT_ROWS = 26
MAX_INSTITUTIONAL_ROWS = 20

ARTIFACT_ID = "ownership_openbb"

# Each dataset is (key, OpenBB call, provider, row cap). They are queried
# independently and a failure in one is a warning, not a failed kind: OpenBB's
# `major_holders` currently raises a validation error on its own data model and
# stockgrid's short-volume endpoint returns HTML, so partial results are the
# normal case rather than the exception.
DATASETS: tuple[tuple[str, str, str, int], ...] = (
    ("institutional", "obb.equity.ownership.institutional(symbol, provider='fmp')",
     "fmp", MAX_INSTITUTIONAL_ROWS),
    ("insider_trading",
     "obb.equity.ownership.insider_trading(symbol, provider='fmp', limit=200)",
     "fmp", MAX_INSIDER_ROWS),
    ("short_interest", "obb.equity.shorts.short_interest(symbol, provider='finra')",
     "finra", MAX_SHORT_ROWS),
)

# Runs inside the OpenBB interpreter. Prints ONE json object on stdout; every
# per-dataset failure is captured into `errors` rather than raised, so one dead
# provider cannot cost us the other two.
_RUNNER = '''
import json, os, sys, warnings
warnings.filterwarnings("ignore")

def _jsonable(value):
    """datetimes, Decimals and numpy scalars -> something json.dump accepts."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)

DATE_KEYS = ("settlement_date", "transaction_date", "filing_date",
             "period_ending", "date")

def _newest_first(rows):
    """Sort by whichever date field the provider uses, newest first.

    Row order is NOT consistent across OpenBB providers: FMP returns Form 4
    filings newest-first, FINRA returns short interest oldest-first. Capping a
    raw list therefore kept FINRA's OLDEST rows and threw away five years of
    recent settlements — the artifact reported 2021 short interest as current.
    Rows without any recognizable date sort last rather than being dropped.
    """
    key = next((k for k in DATE_KEYS if rows and k in rows[0]), None)
    if key is None:
        return rows
    return sorted(rows, key=lambda r: (r.get(key) is not None, str(r.get(key) or "")),
                  reverse=True)

out = {"data": {}, "errors": {}, "providers": {}}
try:
    from openbb import obb
except Exception as exc:
    print(json.dumps({"fatal": "openbb import failed: %s: %s"
                      % (type(exc).__name__, exc)}))
    sys.exit(0)

for name in ("fmp", "intrinio", "polygon", "tiingo", "alpha_vantage"):
    key = os.environ.get(name.upper() + "_API_KEY")
    if key:
        try:
            setattr(obb.user.credentials, name + "_api_key", key)
        except Exception:
            pass

symbol = os.environ["OPENBB_SYMBOL"]
for name, expr, provider, cap in json.loads(os.environ["OPENBB_DATASETS"]):
    try:
        result = eval(expr)
        rows = [r.model_dump() if hasattr(r, "model_dump") else dict(r)
                for r in result.results]
        rows = _newest_first(rows)[:cap]
        out["data"][name] = json.loads(json.dumps(rows, default=_jsonable))
        out["providers"][name] = provider
    except Exception as exc:
        out["errors"][name] = ("%s: %s" % (type(exc).__name__, exc))[:200]

print(json.dumps(out))
'''


def openbb_python() -> str | None:
    """The interpreter to run OpenBB in, or None if there is not one.

    `OPENBB_PYTHON` wins so an operator can point at any environment that has
    OpenBB. Otherwise this project's own interpreter is used, but only when
    `openbb` actually imports there — checked rather than assumed, because the
    failure mode of guessing is a 180-second subprocess timeout per build.
    """
    configured = (os.environ.get("OPENBB_PYTHON") or "").strip()
    if configured:
        return configured if Path(configured).exists() else None

    try:
        import openbb  # noqa: F401
    except ImportError:
        return None
    return sys.executable


def _subprocess_env(symbol: str) -> dict[str, str]:
    """Environment for the runner: the symbol, the datasets, and any keys.

    Provider keys are forwarded from this process's environment. They are NOT
    put in argv — `ps` shows argv to every user on the box.
    """
    env = dict(os.environ)
    env["OPENBB_SYMBOL"] = symbol
    env["OPENBB_DATASETS"] = json.dumps(
        [[name, expr, provider, cap] for name, expr, provider, cap in DATASETS])
    # OpenBB reads its own config from HOME; leaving it alone means an operator's
    # `obb.account.login` credentials keep working.
    return env


def run_openbb(symbol: str, *, interpreter: str | None = None,
               runner=subprocess.run) -> tuple[dict | None, str | None]:
    """Query OpenBB out of process. Returns `(payload, error)`.

    Never raises: a missing interpreter, a crash, a timeout and unparseable
    stdout are all `(None, reason)`, because this kind degrades.
    """
    python = interpreter or openbb_python()
    if not python:
        return None, ("openbb_unavailable: no interpreter with OpenBB "
                      "(set OPENBB_PYTHON)")
    if not shutil.which(python) and not Path(python).exists():
        return None, f"openbb_unavailable: interpreter not found: {python}"

    try:
        proc = runner([python, "-c", _RUNNER], capture_output=True, text=True,
                      timeout=SUBPROCESS_TIMEOUT, env=_subprocess_env(symbol),
                      check=False)
    except subprocess.TimeoutExpired:
        return None, f"openbb_timeout: no answer within {SUBPROCESS_TIMEOUT}s"
    except OSError as exc:
        return None, f"openbb_unavailable: cannot run {python} ({exc})"

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return None, f"openbb_error: exit {proc.returncode}: {tail[-1] if tail else ''}"[:300]

    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, "openbb_error: runner produced no parseable JSON"

    if payload.get("fatal"):
        return None, f"openbb_unavailable: {payload['fatal']}"[:300]
    return payload, None


def fetch_ownership(ticker: str, ticker_dir: Path, state: dict,
                    **kwargs) -> tuple[bool, list[Path], str | None]:
    """Fetch ownership, insider trades and short interest -> `structured/`.

    Returns `(ok, paths, err)` per §11.1. A partial result is a SUCCESS WITH
    WARNING: two datasets out of three is genuinely useful, and failing the kind
    would throw away the two that worked.
    """
    payload, err = run_openbb(ticker.upper(),
                              interpreter=kwargs.get("interpreter"),
                              runner=kwargs.get("runner") or subprocess.run)
    if payload is None:
        return False, [], err

    data = payload.get("data") or {}
    errors = payload.get("errors") or {}
    if not data:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "no data"
        return False, [], f"openbb_empty: every dataset failed ({detail})"[:300]

    now = datetime.now(timezone.utc)
    body = {
        "symbol": ticker.upper(),
        "datasets": data,
        "providers": payload.get("providers") or {},
        # Recorded IN the artifact, not just returned: a reader six months from
        # now has to be able to tell "no insider selling" from "the insider
        # feed was down that day".
        "unavailable": errors,
    }

    meta = StructuredMeta(
        id=ARTIFACT_ID,
        ticker=ticker.upper(),
        producer="fetch",
        title=f"{ticker.upper()} ownership, insider activity and short interest",
        source="OpenBB",
        url="https://docs.openbb.co/platform/reference/equity/ownership",
        as_of=now.date().isoformat(),
        fetched_at=now.isoformat(),
        provider_tool="lib/fetchers/ownership.py",
        fetch_cmd=f"uv run python sra.py prefetch {ticker.upper()} --kinds ownership",
        request={"symbol": ticker.upper(),
                 "datasets": sorted(data),
                 "providers": payload.get("providers") or {}},
    )
    path = write_structured(ticker_dir, meta, body)
    record_fetch(state, "ownership", ARTIFACT_ID, now,
                 {"policy_days": OWNERSHIP_POLICY_DAYS})

    warning = None
    if errors:
        warning = ("openbb_partial: "
                   + "; ".join(f"{k} unavailable ({v})" for k, v in errors.items()))[:300]
    return True, [path], warning
