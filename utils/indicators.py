"""utils/indicators.py — standard, well-known formulas only (Wilder's ATR)."""
from __future__ import annotations
from typing import List
from data.providers import Candle


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(candles: List[Candle], period: int = 14) -> List[float]:
    """Wilder's ATR. Returns a list aligned to `candles` (first `period` entries are None-equivalent -> 0.0)."""
    if len(candles) < period + 1:
        return [0.0] * len(candles)

    trs = [0.0]
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i - 1].close, candles[i].high, candles[i].low))

    atr_values = [0.0] * len(candles)
    seed = sum(trs[1:period + 1]) / period
    atr_values[period] = seed
    for i in range(period + 1, len(candles)):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + trs[i]) / period
    return atr_values


def percentile(values: List[float], pct: float) -> float:
    """pct in [0,1]. Simple linear-interpolation percentile, no external deps."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)
