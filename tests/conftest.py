from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import pytest

TICKER_SUBDIRS = (
    "sources", "sources/archive", "structured",
    "derived", "derived/answers", "derived/peers",
    "wiki", "wiki/entities", "charts", "charts/candidates",
    "reports", "research",
)

@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)

@pytest.fixture
def tmp_ticker_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "PANW"
    for sub in TICKER_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root

@pytest.fixture
def tmp_macro_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "_MACRO"
    for sub in ("sources", "sources/archive", "structured"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def _no_live_fallback_tiers(monkeypatch, request):
    """Keep the harvest's heavier tiers off the network during tests.

    `fetch_batch` defaults tiers 2 and 3 to the REAL headless browser and the
    real Bright Data proxy, which is right in production and a trap in a test:
    any test that injects a tier-1 stand-in which fails — and several do, on
    purpose — would escalate and make a live request. One such test was quietly
    fetching bloomberg.com.

    Autouse, so a test written later inherits the protection without knowing the
    ladder exists. Tests that exercise escalation pass their own `tier2`/`tier3`
    explicitly, which takes precedence over these defaults; the integration
    tests opt back in with `@pytest.mark.integration`.
    """
    if "integration" in request.keywords:
        return

    def _unavailable(urls):
        return {u: (False, None,
                    "playwright_unavailable: disabled in tests") for u in urls}

    monkeypatch.setattr("lib.fetchers.urls._browser_tier", _unavailable)
    monkeypatch.setattr("lib.fetchers.urls._brightdata_tier", _unavailable)
