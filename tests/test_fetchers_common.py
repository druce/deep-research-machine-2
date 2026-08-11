"""Tests for shared fetcher helpers."""
from pathlib import Path

import numpy as np
import pandas as pd

from lib.fetchers.common import (
    ESTIMATE_PERIOD_LABELS, as_float, find_prior_source, frame_by_period,
    json_safe, source_exists, statement_to_dict,
)


def test_json_safe_coercions():
    assert json_safe(np.float64(1.5)) == 1.5
    assert json_safe(pd.Timestamp("2026-07-30")) == "2026-07-30T00:00:00"
    assert json_safe(float("nan")) is None
    # ±Infinity is not JSON either (write_structured rejects it), and pandas does
    # not consider it missing, so json_safe has to drop it explicitly.
    assert json_safe(float("inf")) is None
    assert json_safe(float("-inf")) is None
    assert json_safe(np.float32("inf")) is None
    assert json_safe([float("inf"), 2.0]) == [None, 2.0]
    assert json_safe([np.int64(3), None]) == [3, None]
    assert json_safe("text") == "text"


def test_as_float():
    assert as_float("3.25") == 3.25
    assert as_float(None) is None
    assert as_float([1, 2]) is None


def test_statement_to_dict():
    df = pd.DataFrame(
        {pd.Timestamp("2025-07-31"): [8027000000.0, np.nan],
         pd.Timestamp("2024-07-31"): [6893000000.0, 440000000.0]},
        index=["TotalRevenue", "NetIncome"],
    )
    out = statement_to_dict(df)
    assert out["2025-07-31"]["TotalRevenue"] == 8027000000.0
    assert out["2025-07-31"]["NetIncome"] is None
    assert list(out) == ["2025-07-31", "2024-07-31"]


def test_frame_by_period():
    df = pd.DataFrame({"avg": [1.2, 1.4]}, index=["0q", "+1y"])
    out = frame_by_period(df, ESTIMATE_PERIOD_LABELS)
    assert out == {"current_quarter": {"avg": 1.2}, "next_fiscal_year": {"avg": 1.4}}
    assert frame_by_period(None, ESTIMATE_PERIOD_LABELS) == {}


def test_find_prior_source(tmp_ticker_dir: Path):
    assert find_prior_source(tmp_ticker_dir, "wikipedia") is None
    (tmp_ticker_dir / "sources" / "2026-04-01_wikipedia.md").write_text("x")
    (tmp_ticker_dir / "sources" / "2026-06-15_wikipedia.md").write_text("x")
    (tmp_ticker_dir / "sources" / "2026-06-20_news_yahoo.md").write_text("x")
    assert find_prior_source(tmp_ticker_dir, "wikipedia") == "2026-06-15_wikipedia"
    assert source_exists(tmp_ticker_dir, "2026-04-01_wikipedia")
    assert not source_exists(tmp_ticker_dir, "2026-04-02_wikipedia")


def test_find_prior_source_ignores_the_archive(tmp_ticker_dir: Path):
    """`supersedes:` must point at the CURRENT document being replaced. An
    archived file is already superseded; pointing a new source at it would
    chain onto a dead link and leave the live document unarchived."""
    (tmp_ticker_dir / "sources" / "archive" / "2026-04-01_wikipedia_2026-06-15.md"
     ).write_text("x")
    assert find_prior_source(tmp_ticker_dir, "wikipedia") is None
    (tmp_ticker_dir / "sources" / "2026-06-15_wikipedia.md").write_text("x")
    assert find_prior_source(tmp_ticker_dir, "wikipedia") == "2026-06-15_wikipedia"


def test_source_exists_sees_archived_ids(tmp_ticker_dir: Path):
    """`source_exists` guards the immutable-write paths, so it has to agree
    with `write_source`, which raises FileExistsError for an id taken in
    EITHER sources/ or sources/archive/ (§5: ids are unique across both).
    Checking only sources/ would let a caller decide "safe to write" and then
    crash on an id that was archived earlier."""
    (tmp_ticker_dir / "sources" / "archive" / "2026-04-01_wikipedia_2026-06-15.md"
     ).write_text("x")
    assert source_exists(tmp_ticker_dir, "2026-04-01_wikipedia")
