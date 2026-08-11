from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import TICKER_SUBDIRS


def test_tmp_ticker_dir(tmp_ticker_dir: Path) -> None:
    """Verify tmp_ticker_dir fixture creates the expected directory tree."""
    # Check the path ends in data/PANW
    assert tmp_ticker_dir.name == "PANW"
    assert tmp_ticker_dir.parent.name == "data"

    # Check all expected subdirectories exist
    for sub in TICKER_SUBDIRS:
        subdir = tmp_ticker_dir / sub
        assert subdir.exists(), f"Expected subdirectory {sub} not found"
        assert subdir.is_dir(), f"Expected {sub} to be a directory"


def test_tmp_macro_dir(tmp_macro_dir: Path) -> None:
    """Verify tmp_macro_dir fixture creates the expected directory tree."""
    # Check the path ends in data/_MACRO
    assert tmp_macro_dir.name == "_MACRO"
    assert tmp_macro_dir.parent.name == "data"

    # Check expected subdirectories exist
    expected_subs = ("sources", "sources/archive", "structured")
    for sub in expected_subs:
        subdir = tmp_macro_dir / sub
        assert subdir.exists(), f"Expected subdirectory {sub} not found"
        assert subdir.is_dir(), f"Expected {sub} to be a directory"


def test_fixed_now(fixed_now: datetime) -> None:
    """Verify fixed_now fixture returns the correct datetime."""
    expected = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert fixed_now == expected
    assert fixed_now.tzinfo == timezone.utc
    assert fixed_now.tzinfo is not None  # Confirm it's timezone-aware
