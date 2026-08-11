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
