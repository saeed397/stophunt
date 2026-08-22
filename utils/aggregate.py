"""utils/aggregate.py
Combines N consecutive REAL candles into one larger candle (e.g. four native
60-minute Yahoo Finance candles -> one 4h candle). This is standard OHLCV
resampling (Open=first, High=max, Low=min, Close=last, Volume=sum) — it does
not invent any price; it only re-buckets values that were already returned by
the provider. Incomplete trailing groups (fewer than `factor` candles) are
dropped rather than emitted as a partial/misleading bar.
"""
from __future__ import annotations
from typing import List, TypeVar

# Deliberately NOT importing data.providers.Candle here (that module imports
# this one) — this function only relies on the four OHLCV attributes plus
# timestamp/closed, which is a structural (duck-typed) requirement, not a
# hard dependency on the concrete Candle class.
CandleLike = TypeVar("CandleLike")


def aggregate_candles(candles: List[CandleLike], factor: int) -> List[CandleLike]:
    if factor <= 1:
        return candles
    from data.providers import Candle  # local import: safe, avoids circular import at module load time
    out: List[Candle] = []
    for i in range(0, len(candles) - factor + 1, factor):
        group = candles[i:i + factor]
        out.append(Candle(
            timestamp=group[0].timestamp,
            open=group[0].open,
            high=max(c.high for c in group),
            low=min(c.low for c in group),
            close=group[-1].close,
            volume=sum(c.volume for c in group),
            closed=all(c.closed for c in group),
        ))
    return out
