"""
data/validator.py
==================
Implements the RAW DATA -> DATA ACCEPTED pipeline described in the spec
(3.17 Data Validation Engine, 3.18 Missing Data, 3.23 ValidatedMarketData).

Nothing here fabricates a candle. A gap is recorded as DATA_GAP, never filled.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple
from data.providers import Candle


@dataclass
class DataQualityReport:
    asset: str
    timeframe: str
    total_candles: int
    duplicates_removed: int
    gaps: List[Tuple[int, int]] = field(default_factory=list)   # (gap_start_ts, gap_end_ts)
    ohlc_violations: int = 0
    accepted: bool = False
    reason: str = ""


class DataValidationError(Exception):
    pass


def _timeframe_seconds(tf: str) -> int:
    mapping = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
               "1d": 86400, "1w": 604800, "1M": 2592000}
    return mapping[tf]


def validate_candles(candles: List[Candle], asset: str, timeframe: str) -> Tuple[List[Candle], DataQualityReport]:
    if not candles:
        raise DataValidationError("اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد.")

    # 1. Timestamp check + duplicate check
    seen = set()
    deduped: List[Candle] = []
    dup_count = 0
    for c in sorted(candles, key=lambda x: x.timestamp):
        if c.timestamp in seen:
            dup_count += 1
            continue
        seen.add(c.timestamp)
        deduped.append(c)

    # 2. OHLC consistency: High >= max(Open, Close), Low <= min(Open, Close)
    violations = 0
    clean: List[Candle] = []
    for c in deduped:
        if c.high >= max(c.open, c.close) and c.low <= min(c.open, c.close) and c.high >= c.low:
            clean.append(c)
        else:
            violations += 1  # dropped, never "corrected" by guessing

    # 3. Missing candle / gap check (never synthesized, only recorded)
    step = _timeframe_seconds(timeframe)
    gaps: List[Tuple[int, int]] = []
    for i in range(1, len(clean)):
        expected = clean[i - 1].timestamp + step
        actual = clean[i].timestamp
        if actual > expected:
            gaps.append((clean[i - 1].timestamp, clean[i].timestamp))  # DATA_GAP

    # 4. Closed-candle validation: never let the still-forming candle act as a closed one
    clean = [c for c in clean if c.closed] if clean and not clean[-1].closed else clean

    report = DataQualityReport(
        asset=asset,
        timeframe=timeframe,
        total_candles=len(clean),
        duplicates_removed=dup_count,
        gaps=gaps,
        ohlc_violations=violations,
    )

    if len(clean) == 0:
        report.accepted = False
        report.reason = "No candles survived validation."
        raise DataValidationError(report.reason)

    report.accepted = True
    return clean, report
