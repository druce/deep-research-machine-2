#!/usr/bin/env python3
"""Technical indicators computed from the stored prices artifact (§11.1, §6.2).

Takes no provider kwarg: its input IS `structured/prices_yahoo.json`, which is
what makes `derived_from: ["prices_yahoo"]` literally true and the whole
computation offline-testable and reproducible.

Chart rendering deliberately does NOT live here. The price exhibit is a gold
artifact built by `lib/charts/` (§16), and keeping plotly out of a bronze
fetcher means a missing headless-Chrome runtime cannot fail evidence gathering.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from lib.fetchers.common import fetch_cmd
from lib.provenance import (
    SOURCE_COMPUTED, StructuredMeta, read_structured, write_structured)
from lib.statefile import record_fetch

if TYPE_CHECKING:  # numpy/talib are imported lazily inside the functions
    import numpy as np

SMA_SHORT_PERIOD, SMA_MEDIUM_PERIOD, SMA_LONG_PERIOD = 20, 50, 200
RSI_PERIOD = 14
MACD_FAST_PERIOD, MACD_SLOW_PERIOD, MACD_SIGNAL_PERIOD = 12, 26, 9
ATR_PERIOD = 14
BOLLINGER_PERIOD, BOLLINGER_STD_DEV = 20, 2
VOLUME_AVERAGE_DAYS = 20
# sra5's "days of data for daily analysis" horizon (config.CHART_HISTORY_DAYS). sra6
# computes over the whole stored series instead — SMA(200) needs more than a year of
# bars — so this records the documented daily-analysis window for downstream readers.
DAILY_WINDOW_DAYS = 365

DEPENDS_ON: tuple[str, ...] = ("prices",)


# --- ported verbatim from sra5 skills/fetch_technical/fetch_technical.py -------------

def _compute_trend_signals(
    latest_close: float,
    sma20_val, sma50_val, sma200_val,
    rsi_val, macd_hist_val,
    bb_upper_val, bb_lower_val,
    vol_latest: float, vol_avg: float,
) -> dict:
    """Derive boolean trend signals from indicator values."""
    signals = {}

    # SMA trend
    if sma50_val is not None and sma200_val is not None:
        signals["golden_cross"] = sma50_val > sma200_val
        signals["death_cross"] = sma50_val <= sma200_val

    # Price vs SMAs
    if sma20_val is not None:
        signals["above_sma20"] = latest_close > sma20_val
    if sma50_val is not None:
        signals["above_sma50"] = latest_close > sma50_val
    if sma200_val is not None:
        signals["above_sma200"] = latest_close > sma200_val

    # RSI zones
    if rsi_val is not None:
        signals["rsi_overbought"] = rsi_val > 70
        signals["rsi_oversold"] = rsi_val < 30

    # MACD signal
    if macd_hist_val is not None:
        signals["macd_bullish"] = macd_hist_val > 0
        signals["macd_bearish"] = macd_hist_val < 0

    # Bollinger position
    if bb_upper_val is not None and bb_lower_val is not None:
        signals["above_upper_bb"] = latest_close > bb_upper_val
        signals["below_lower_bb"] = latest_close < bb_lower_val

    # Volume signal
    if vol_avg > 0:
        signals["volume_above_avg"] = vol_latest > vol_avg

    return signals


def _build_narrative_analysis(
    symbol: str, latest_close: float, latest_date: str,
    sma50_val, sma200_val, rsi_val, macd_hist_val,
    atr_val, bb_upper_val, bb_lower_val,
) -> str:
    """Build a human-readable narrative from computed indicator values."""
    parts = [f"{symbol} closed at ${latest_close:.2f} on {latest_date}."]

    # SMA analysis
    sma_comments = []
    if sma200_val is not None:
        rel = "above" if latest_close > sma200_val else "below"
        pct = ((latest_close - sma200_val) / sma200_val) * 100
        sma_comments.append(
            f"Price is {rel} the 200-day SMA (${sma200_val:.2f}), "
            f"{pct:+.1f}% from it."
        )
    if sma50_val is not None:
        rel = "above" if latest_close > sma50_val else "below"
        sma_comments.append(
            f"Price is {rel} the 50-day SMA (${sma50_val:.2f})."
        )
    if sma_comments:
        parts.append(" ".join(sma_comments))

    # RSI
    if rsi_val is not None:
        if rsi_val > 70:
            rsi_comment = f"RSI({RSI_PERIOD}) is {rsi_val:.1f} (overbought territory)."
        elif rsi_val < 30:
            rsi_comment = f"RSI({RSI_PERIOD}) is {rsi_val:.1f} (oversold territory)."
        else:
            rsi_comment = f"RSI({RSI_PERIOD}) is {rsi_val:.1f} (neutral)."
        parts.append(rsi_comment)

    # MACD
    if macd_hist_val is not None:
        direction = "bullish" if macd_hist_val > 0 else "bearish"
        parts.append(
            f"MACD histogram is {direction} at {macd_hist_val:.2f}."
        )

    # ATR
    if atr_val is not None:
        parts.append(
            f"ATR({ATR_PERIOD}) is ${atr_val:.2f}, indicating "
            f"{'high' if atr_val > latest_close * 0.03 else 'moderate'} "
            f"volatility."
        )

    # Bollinger
    if bb_upper_val is not None and bb_lower_val is not None:
        if latest_close > bb_upper_val:
            bb_comment = "Price is above the upper Bollinger Band (potential overbought)."
        elif latest_close < bb_lower_val:
            bb_comment = "Price is below the lower Bollinger Band (potential oversold)."
        elif bb_upper_val != bb_lower_val:
            bb_pct = (
                (latest_close - bb_lower_val)
                / (bb_upper_val - bb_lower_val)
                * 100
            )
            bb_comment = (
                f"Price is at {bb_pct:.0f}% of the Bollinger Band range "
                f"(${bb_lower_val:.2f} - ${bb_upper_val:.2f})."
            )
        else:
            bb_comment = f"Bollinger Bands are flat at ${bb_upper_val:.2f}."
        parts.append(bb_comment)

    return "\n".join(parts)


# --- end ported section ---------------------------------------------------------------

def _last(arr: np.ndarray) -> float | None:
    """Latest value of a ta-lib output array, or None while the indicator is warming up.

    Uses math.isnan rather than np.isnan so the helper needs no numpy import of its own.
    """
    val = float(arr[-1])
    return None if math.isnan(val) else round(val, 2)



def fetch_technical(
    ticker: str,
    ticker_dir: Path,
    state: dict,
    *,
    now: datetime | None = None,
) -> tuple[bool, list[Path], str | None]:
    """Compute technical indicators from the stored prices artifact, with provenance.

    Every malformed-input path returns the (False, [], msg) contract rather than
    raising: the prices artifact is this fetcher's sole input, and a corrupt or
    truncated one is bad data, not a bug here.
    """
    # Local imports: talib needs the ta-lib C library, which is not guaranteed to exist
    # everywhere this repo runs. Keeping it here means `import sra` and test collection
    # still work without it (same pattern as profile.py/prices.py and yfinance).
    import numpy as np
    import talib

    now = now or datetime.now(timezone.utc)
    prices_path = ticker_dir / "structured" / "prices_yahoo.json"
    if not prices_path.exists():
        return False, [], ("prices_yahoo.json not found — "
                           "run `sra.py prefetch <T> --kinds prices` first")
    # The prices artifact is our sole input; a corrupt or truncated one is bad data,
    # not a bug here. Read, shape-check and numeric-parse it defensively so a
    # malformed file yields the (False, [], msg) contract rather than crashing the
    # prefetch run (read_structured/np.array/[-1] would otherwise raise).
    try:
        _, prices = read_structured(prices_path)
    except (ValueError, KeyError, TypeError) as exc:
        return False, [], f"prices_yahoo.json unreadable: {exc}"
    daily = prices.get("daily") if isinstance(prices, dict) else None
    if not isinstance(daily, dict):
        return False, [], "prices_yahoo.json has no daily series"
    missing = [k for k in ("close", "high", "low", "volume", "dates") if k not in daily]
    if missing:
        return False, [], f"prices_yahoo.json daily series missing: {', '.join(missing)}"

    try:
        close = np.array(daily["close"], dtype=np.float64)
        high = np.array(daily["high"], dtype=np.float64)
        low = np.array(daily["low"], dtype=np.float64)
        volume = np.array(daily["volume"], dtype=np.float64)
    except (ValueError, TypeError) as exc:
        return False, [], f"prices_yahoo.json daily series is not numeric: {exc}"
    if close.size < 2:
        return False, [], "not enough price history to compute indicators"
    # talib combines high/low/close (ATR) and would raise on ragged inputs, so all
    # four series and the dates must be the same length before any indicator runs.
    if not (high.size == low.size == volume.size == close.size):
        return False, [], "prices_yahoo.json OHLCV series have mismatched lengths"
    dates = daily["dates"]
    if not isinstance(dates, list) or len(dates) != close.size:
        return False, [], "prices_yahoo.json dates do not match the close series"

    latest_close = float(close[-1])
    latest_date = dates[-1]

    sma20_val = _last(talib.SMA(close, timeperiod=SMA_SHORT_PERIOD))
    sma50_val = _last(talib.SMA(close, timeperiod=SMA_MEDIUM_PERIOD))
    sma200_val = _last(talib.SMA(close, timeperiod=SMA_LONG_PERIOD))
    rsi_val = _last(talib.RSI(close, timeperiod=RSI_PERIOD))
    macd_line, macd_signal, macd_hist = talib.MACD(
        close, fastperiod=MACD_FAST_PERIOD, slowperiod=MACD_SLOW_PERIOD,
        signalperiod=MACD_SIGNAL_PERIOD)
    atr_val = _last(talib.ATR(high, low, close, timeperiod=ATR_PERIOD))
    bb_upper, bb_middle, bb_lower = talib.BBANDS(
        close, timeperiod=BOLLINGER_PERIOD, nbdevup=BOLLINGER_STD_DEV,
        nbdevdn=BOLLINGER_STD_DEV, matype=0)
    # An all-NaN window would make nanmean warn and serialize as a bare NaN literal
    # (not valid JSON for other readers); 0.0 also reads as "no volume signal" below.
    vol_window = volume[-VOLUME_AVERAGE_DAYS:]
    vol_avg = float(np.nanmean(vol_window)) if np.isfinite(vol_window).any() else 0.0
    vol_latest = float(volume[-1]) if np.isfinite(volume[-1]) else 0.0

    macd_val, macd_sig_val, macd_hist_val = _last(macd_line), _last(macd_signal), _last(macd_hist)
    bb_upper_val, bb_middle_val, bb_lower_val = _last(bb_upper), _last(bb_middle), _last(bb_lower)

    payload = {
        "symbol": ticker.upper(),
        "date": latest_date,
        "close": latest_close,
        "indicators": {
            "sma_20": sma20_val, "sma_50": sma50_val, "sma_200": sma200_val,
            "rsi": rsi_val, "macd": macd_val, "macd_signal": macd_sig_val,
            "macd_histogram": macd_hist_val, "atr": atr_val,
            "bollinger_upper": bb_upper_val, "bollinger_middle": bb_middle_val,
            "bollinger_lower": bb_lower_val,
            "volume_latest": vol_latest, "volume_avg_20d": round(vol_avg, 0),
        },
        "trend_signals": _compute_trend_signals(
            latest_close, sma20_val, sma50_val, sma200_val, rsi_val,
            macd_hist_val, bb_upper_val, bb_lower_val, vol_latest, vol_avg),
        "analysis": _build_narrative_analysis(
            ticker.upper(), latest_close, latest_date, sma50_val, sma200_val,
            rsi_val, macd_hist_val, atr_val, bb_upper_val, bb_lower_val),
    }
    meta = StructuredMeta(
        id="technical_indicators_computed",
        ticker=ticker.upper(),
        producer="compute",
        title=f"{ticker.upper()} technical indicators",
        source=SOURCE_COMPUTED,
        # §6.2: a compute artifact carries no url — it was derived, not fetched.
        provider_tool="lib/fetchers/technical.py",
        fetch_cmd=fetch_cmd(ticker, "technical"),
        computed_at=now.isoformat(),
        # §6.4: as_of is the period end — the last bar the indicators close on.
        as_of=latest_date,
        derived_from=["prices_yahoo"],
    )
    path = write_structured(ticker_dir, meta, payload)
    record_fetch(state, "technical", "technical_indicators_computed", now,
                 {"policy_days": 1})
    return True, [path], None
