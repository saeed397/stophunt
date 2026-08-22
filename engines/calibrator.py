"""
engines/calibrator.py
======================
This module is the direct implementation of Rule #1 (هشدار ۱):

    "تمام سیگنال‌ها ... باید از بک‌تست همان رمزارز و در همان تایم‌فریم صادر شود."

Every tolerance / threshold used downstream (equal-level tolerance, sweep
penetration depth, rejection speed, setup-score weighting) is computed HERE,
from that asset+timeframe's own historical candle distribution — never from a
fixed cross-asset constant. A low-volatility asset and a high-volatility asset
will therefore get numerically different tolerances even though they run the
exact same code path.

If there isn't enough history to calibrate safely, this module raises
CalibrationError instead of silently falling back to a guessed number.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List
from data.providers import Candle
from utils.indicators import atr, percentile
from config import DEFAULTS


class CalibrationError(Exception):
    pass


@dataclass
class AssetTimeframeProfile:
    asset: str
    timeframe: str
    n_candles: int
    atr_series: List[float]
    equal_level_tolerance: float       # absolute price units, derived from this asset's own High/Low deltas
    median_wick_body_ratio: float
    penetration_depth_atr_p50: float   # median historical sweep-wick penetration, in ATR units
    penetration_depth_atr_p75: float
    calibration_window_candles: int = 0     # how many of the most recent candles the stats above were computed from
    regime_shift_flag: bool = False         # True if current volatility differs sharply from the older baseline (see below)
    regime_shift_ratio: float = 1.0         # recent ATR mean / older ATR mean


def calibrate(candles: List[Candle], asset: str, timeframe: str,
              rolling_window: int = None) -> AssetTimeframeProfile:
    """
    Rule #1 compliance, made regime-aware:

    All statistical thresholds below (equal-level tolerance, wick/body ratio,
    penetration depth) are computed ONLY from the most recent `rolling_window`
    candles of THIS asset/timeframe — not its entire multi-year history. A coin
    that was quiet a year ago and is violently volatile today (or vice versa)
    must get thresholds sized to its CURRENT behavior, or a stale average would
    make SL buffers either dangerously tight or needlessly wide. `n_candles` /
    `atr_series` still reflect the full requested history (needed for swing and
    liquidity-level detection, which should see as much structure as possible),
    but everything that sizes an SL/TP or a tolerance is windowed.

    We also compute a `regime_shift_flag`: if the recent window's average ATR
    differs from the immediately-preceding window's average ATR by more than
    2x in either direction, this is surfaced to the UI/caller so a human can
    decide whether to trust a signal generated right after a volatility regime
    change (e.g. right after a listing pump or a flash crash), instead of the
    engine silently treating old and new volatility as equivalent.
    """
    if len(candles) < DEFAULTS.min_lookback_candles:
        raise CalibrationError(
            f"Only {len(candles)} closed candles available for {asset}/{timeframe}; "
            f"minimum {DEFAULTS.min_lookback_candles} required for a data-driven calibration. "
            f"اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."
        )

    window = rolling_window or DEFAULTS.rolling_calibration_window
    recent = candles[-window:] if len(candles) > window else candles
    offset = len(candles) - len(recent)   # global index of recent[0]

    atr_series = atr(candles, period=DEFAULTS.atr_period)

    # --- Equal-level tolerance, derived ONLY from the recent window's own High-High / Low-Low deltas ---
    high_deltas = [abs(recent[i].high - recent[i - 1].high) for i in range(1, len(recent))]
    low_deltas = [abs(recent[i].low - recent[i - 1].low) for i in range(1, len(recent))]
    combined_deltas = high_deltas + low_deltas
    tolerance = percentile(combined_deltas, DEFAULTS.equal_level_tolerance_pctile)

    # --- Wick/body ratio distribution, recent window only ---
    ratios = []
    for c in recent:
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low
        wick = max(upper_wick, lower_wick)
        if body > 0:
            ratios.append(wick / body)
    median_wr = percentile(ratios, 0.5) if ratios else 0.0

    # --- Historical penetration depth within the recent window, ATR-normalized
    # using each bar's OWN globally-aligned ATR value (so a penetration during a
    # violent bar is correctly scaled relative to volatility at that exact moment).
    penetrations_atr = []
    lookback_extreme = 20
    for i in range(lookback_extreme, len(recent)):
        global_i = offset + i
        w = recent[i - lookback_extreme:i]
        prior_high = max(c.high for c in w)
        prior_low = min(c.low for c in w)
        a = atr_series[global_i] if global_i < len(atr_series) and atr_series[global_i] > 0 else None
        if not a:
            continue
        c = recent[i]
        if c.high > prior_high:
            penetrations_atr.append((c.high - prior_high) / a)
        if c.low < prior_low:
            penetrations_atr.append((prior_low - c.low) / a)

    p50 = percentile(penetrations_atr, 0.5) if penetrations_atr else 0.0
    p75 = percentile(penetrations_atr, 0.75) if penetrations_atr else 0.0

    # --- Regime-shift detection: compare recent-window ATR to the ATR of the
    # equally-sized window immediately BEFORE it (if enough history exists).
    regime_flag = False
    regime_ratio = 1.0
    prior_start = offset - len(recent)
    if prior_start >= 0:
        recent_atr_vals = [v for v in atr_series[offset:offset + len(recent)] if v > 0]
        prior_atr_vals = [v for v in atr_series[prior_start:offset] if v > 0]
        if recent_atr_vals and prior_atr_vals:
            recent_mean = sum(recent_atr_vals) / len(recent_atr_vals)
            prior_mean = sum(prior_atr_vals) / len(prior_atr_vals)
            if prior_mean > 0:
                regime_ratio = recent_mean / prior_mean
                regime_flag = regime_ratio >= 2.0 or regime_ratio <= 0.5

    return AssetTimeframeProfile(
        asset=asset,
        timeframe=timeframe,
        n_candles=len(candles),
        atr_series=atr_series,
        equal_level_tolerance=tolerance,
        median_wick_body_ratio=median_wr,
        penetration_depth_atr_p50=p50,
        penetration_depth_atr_p75=p75,
        calibration_window_candles=len(recent),
        regime_shift_flag=regime_flag,
        regime_shift_ratio=round(regime_ratio, 3),
    )
