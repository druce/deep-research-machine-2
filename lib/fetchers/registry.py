"""Fetcher registry, dependency waves, and the prefetch runner (spec §11.1, §7.1).

The registry is data first: `KIND_ORDER`, `DEFAULT_KINDS`, `FATAL_KINDS` and
`KIND_STAGES` are pinned by the spec and by `tests/test_registry.py`, so adding
a kind is a deliberate act rather than a side effect of creating a module.

Concurrency model
-----------------
§11.1 says fetchers run as in-process futures and **only the main thread
mutates shared state**. Ported fetchers record their own results by calling
`record_fetch(state, ...)` on the state they are handed, which in a worker
thread would be exactly the race that rule forbids.

Both hold here because each fetcher receives a private deep copy of state and
the main thread merges the delta back as each future lands. The fetchers keep
their ported internals, the shared dict is touched by one thread only, and
`save_state` runs after every fetcher returns — success, warning, clean
failure, or crash (§7.1), so a crash cannot leave completed artifacts
unrecorded.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from lib.statefile import save_state

# A fetcher: fetch_x(ticker, ticker_dir, state, **kw) -> (ok, paths, err) (§11.1).
#   (True,  paths, None)  success
#   (True,  paths, err)   success with warning
#   (False, paths, err)   failure
Fetcher = Callable[..., "tuple[bool, list[Path], str | None]"]

# Execution order within a wave. §11.1's table, in registry order.
KIND_ORDER: tuple[str, ...] = (
    "profile", "prices", "technical", "financials", "estimates", "targets",
    "calendar", "peers", "filings", "transcript", "wikipedia", "news",
    "perplexity",
)

# perplexity is an opt-in supplement: the primary prefetch web research runs
# through the deep-research Workflow (§11.2).
DEFAULT_KINDS: list[str] = [k for k in KIND_ORDER if k != "perplexity"]

# §11.1 minimum viable input. Failure of one of these stops /sra-build; every
# other kind degrades. Exposed for the build skill — `prefetch` itself only
# reports, and does not decide what is fatal.
FATAL_KINDS: tuple[str, ...] = ("profile", "prices", "financials")

# A fetcher that completes in stages records each stage under its own state key.
# peers is the only one: the deterministic gather writes `peers_candidates` and
# the model-driven `peers-select` writes `peers`, so `peers` never points at an
# unranked table. Prefetch has to know both keys, or a ticker that was gathered
# but not selected looks never-fetched and re-runs the whole 40-70-call gather
# on every --stale-only sweep, forever.
KIND_STAGES: dict[str, tuple[str, ...]] = {"peers": ("peers", "peers_candidates")}

# ...and the reverse: which registry kind refreshes a stage key that went stale.
STAGE_OF: dict[str, str] = {stage: kind
                            for kind, stages in KIND_STAGES.items()
                            for stage in stages}


class CycleError(ValueError):
    """Raised when DEPENDS_ON edges form a cycle, so no wave order exists."""


def _build_registry() -> dict[str, Fetcher]:
    """Deferred so `load_fetchers` can be defined below without a forward ref."""
    return load_fetchers()


# Kinds whose module name differs from the kind name.
_MODULE_OF: dict[str, str] = {"financials": "fundamentals", "filings": "edgar"}


def load_fetchers() -> dict[str, Fetcher]:
    """Import the fetcher modules that exist and collect their `fetch_<kind>`.

    Modules land across Tasks 5.3-5.5. A kind whose module is not present yet
    is simply absent from the registry, so `prefetch` reports it as an unknown
    kind rather than the whole CLI failing at import time — which would take
    every unrelated command down with it.

    Returned in `KIND_ORDER`, since insertion order is the within-wave
    execution order.
    """
    import importlib

    found: dict[str, Fetcher] = {}
    for kind in KIND_ORDER:
        try:
            module = importlib.import_module(
                f"lib.fetchers.{_MODULE_OF.get(kind, kind)}")
        except ImportError:
            continue
        fn = getattr(module, f"fetch_{kind}", None)
        if callable(fn):
            found[kind] = fn
    return found


# The live registry: kind -> fetcher, in KIND_ORDER. Grows as fetcher modules
# land. `sra.py` imports this; tests replace it wholesale with stubs.
FETCHERS: dict[str, Fetcher] = _build_registry()


def module_depends_on(kind: str) -> tuple[str, ...]:
    """The `DEPENDS_ON` a fetcher module declares (§11.1), or `()`."""
    import importlib

    try:
        module = importlib.import_module(f"lib.fetchers.{_MODULE_OF.get(kind, kind)}")
    except ImportError:
        return ()
    return tuple(getattr(module, "DEPENDS_ON", ()))


def dependency_map(kinds: list[str] | None = None) -> dict[str, tuple[str, ...]]:
    """Declared dependencies for the given kinds, read from their modules."""
    return {kind: module_depends_on(kind) for kind in (kinds or list(KIND_ORDER))}


def waves(kinds: list[str], deps: dict[str, tuple[str, ...]]) -> list[list[str]]:
    """Group `kinds` into dependency waves; everything in a wave may run at once.

    A dependency NOT in `kinds` is ignored rather than pulled in: `prefetch T
    --kinds technical` has to run against the prices artifact already on disk,
    because a `--kinds` request is explicit, not a hint.

    Order within a wave follows `kinds`, so the same request always produces
    the same grouping and the same scheduling.
    """
    remaining = list(kinds)
    wanted = set(kinds)
    done: set[str] = set()
    out: list[list[str]] = []

    while remaining:
        wave = [k for k in remaining
                if all(d in done for d in deps.get(k, ()) if d in wanted)]
        if not wave:
            raise CycleError(
                f"dependency cycle among {', '.join(sorted(remaining))}: no kind "
                f"has all of its requested dependencies satisfied"
            )
        out.append(wave)
        done.update(wave)
        remaining = [k for k in remaining if k not in done]
    return out


def _merge_state(shared: dict, before: dict, after: dict) -> None:
    """Copy a worker's state changes into the shared dict (main thread only).

    Compares the worker's result against the snapshot it started from and
    copies across only what actually changed, so two fetchers in one wave
    cannot clobber each other's entries by writing back whole sections.

    `report` is excluded: `sections_dirty` is the driver's bookkeeping, not a
    fetcher's, and a stale copy of it written back would resurrect sections a
    concurrent command had just consumed.
    """
    for section in ("data", "derived", "wiki"):
        for key, value in (after.get(section) or {}).items():
            if (before.get(section) or {}).get(key) != value:
                shared.setdefault(section, {})[key] = value

    for key, value in after.items():
        if key in ("data", "derived", "wiki", "report"):
            continue
        if before.get(key) != value:
            shared[key] = value


def run_prefetch(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    kinds: list[str],
    fetchers: dict[str, Fetcher],
    deps: dict[str, tuple[str, ...]],
    *,
    extra_kwargs: dict[str, dict] | None = None,
    on_commit: Callable[[dict], None] | None = None,
    **common_kwargs,
) -> dict:
    """Run `kinds` in dependency waves and return §11.1's summary dict.

    Returns `{fetched, skipped, errors, warnings}`. `skipped` is filled by the
    caller, which knows what `--stale-only` dropped; this function only reports
    what it ran.

    Every fetcher outcome is recorded and the run continues: a fetcher bug must
    not cost the work that already succeeded (§7.1). An uncaught exception
    becomes `errors[kind] = "<kind> crashed: <exc>"`.

    `extra_kwargs` targets one kind (peers takes `user_peers`); `common_kwargs`
    reach every fetcher (`now=` for tests).
    """
    unknown = [k for k in kinds if k not in fetchers]
    if unknown:
        raise KeyError(f"unknown kinds: {', '.join(unknown)}")

    extra_kwargs = extra_kwargs or {}
    fetched: list[str] = []
    errors: dict[str, str] = {}
    warnings: dict[str, str] = {}

    for wave in waves(kinds, deps):
        # One snapshot of where the wave started, plus a private working copy
        # per fetcher — all taken in the main thread before any worker runs, so
        # no member of a wave can observe another's half-applied writes.
        wave_before = copy.deepcopy(state)
        working = {kind: copy.deepcopy(state) for kind in wave}

        def call(kind: str):
            return fetchers[kind](ticker, ticker_dir, working[kind],
                                  **extra_kwargs.get(kind, {}), **common_kwargs)

        with ThreadPoolExecutor(max_workers=max(1, len(wave))) as pool:
            futures = {kind: pool.submit(call, kind) for kind in wave}
            results: dict[str, tuple[object, Exception | None]] = {}
            for kind in wave:
                try:
                    results[kind] = (futures[kind].result(), None)
                except Exception as exc:  # noqa: BLE001 — any fetcher bug
                    results[kind] = (None, exc)

        # Main thread only, in wave order so a rerun commits identically.
        for kind in wave:
            outcome, exc = results[kind]
            if exc is not None:
                # A fetcher bug must not cost the run. Its partial state writes
                # are still merged: it may have completed one artifact and
                # crashed on the next, and §7.1 exists so that work is not lost.
                errors[kind] = f"{kind} crashed: {exc}"
            else:
                ok, _paths, err = outcome  # type: ignore[misc]
                if ok:
                    fetched.append(kind)
                    if err:
                        warnings[kind] = err
                else:
                    errors[kind] = err
            _merge_state(state, wave_before, working[kind])

            save_state(ticker_dir, state)
            if on_commit is not None:
                on_commit(state)

    return {"fetched": fetched, "skipped": [], "errors": errors, "warnings": warnings}
