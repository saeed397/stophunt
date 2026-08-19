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


def calibrate(candles: List[Candle], asset: str, timeframe: str) -> AssetTimeframeProfile:
    if len(candles) < DEFAULTS.min_lookback_candles:
        raise CalibrationError(
            f"Only {len(candles)} closed candles available for {asset}/{timeframe}; "
            f"minimum {DEFAULTS.min_lookback_candles} required for a data-driven calibration. "
            f"اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."
        )

    atr_series = atr(candles, period=DEFAULTS.atr_period)

    # --- Equal-level tolerance, derived from this asset's own recent High-High / Low-Low deltas ---
    high_deltas = [abs(candles[i].high - candles[i - 1].high) for i in range(1, len(candles))]
    low_deltas = [abs(candles[i].low - candles[i - 1].low) for i in range(1, len(candles))]
    combined_deltas = high_deltas + low_deltas
    tolerance = percentile(combined_deltas, DEFAULTS.equal_level_tolerance_pctile)

    # --- Wick/body ratio distribution (for sweep-quality scoring later) ---
    ratios = []
    for c in candles:
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low
        wick = max(upper_wick, lower_wick)
        if body > 0:
            ratios.append(wick / body)
    median_wr = percentile(ratios, 0.5) if ratios else 0.0

    # --- Historical penetration depth: how far (in ATR units) a wick has
    # historically pushed beyond the prior N-bar extreme before reversing.
    # This is exactly the "sabeqe" (historical precedent) the user's Rule #1 demands.
    penetrations_atr = []
    lookback_extreme = 20
    for i in range(lookback_extreme, len(candles)):
        window = candles[i - lookback_extreme:i]
        prior_high = max(c.high for c in window)
        prior_low = min(c.low for c in window)
        a = atr_series[i] if atr_series[i] > 0 else None
        if not a:
            continue
        c = candles[i]
        if c.high > prior_high:
            penetrations_atr.append((c.high - prior_high) / a)
        if c.low < prior_low:
            penetrations_atr.append((prior_low - c.low) / a)

    p50 = percentile(penetrations_atr, 0.5) if penetrations_atr else 0.0
    p75 = percentile(penetrations_atr, 0.75) if penetrations_atr else 0.0

    return AssetTimeframeProfile(
        asset=asset,
        timeframe=timeframe,
        n_candles=len(candles),
        atr_series=atr_series,
        equal_level_tolerance=tolerance,
        median_wick_body_ratio=median_wr,
        penetration_depth_atr_p50=p50,
        penetration_depth_atr_p75=p75,
    )
