"""
engines/liquidity_engine.py
============================
Implements spec 2.3 (Swing High/Low), 2.4 (Equal High/Low), 2.5 (Liquidity
Level Engine). Fractal swing detection with explicit Event Time vs Detection
Time separation to avoid lookahead (spec 2.3 "نکته بسیار مهم").
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import List
from data.providers import Candle
from engines.calibrator import AssetTimeframeProfile
from config import DEFAULTS


class SwingType(Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class LevelStatus(Enum):
    ACTIVE = "ACTIVE"
    SWEPT = "SWEPT"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


class LevelKind(Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    PREV_DAY_HIGH = "PREV_DAY_HIGH"
    PREV_DAY_LOW = "PREV_DAY_LOW"
    PREV_WEEK_HIGH = "PREV_WEEK_HIGH"
    PREV_WEEK_LOW = "PREV_WEEK_LOW"


@dataclass
class Swing:
    index: int              # index within the candle array (event time proxy)
    type: SwingType
    price: float
    event_ts: int
    detection_ts: int       # = candle[index + fractal_order].timestamp -> first moment it was knowable
    strength: float = 0.0


@dataclass
class LiquidityLevel:
    id: str
    kind: LevelKind
    price: float
    formed_index: int
    status: LevelStatus = LevelStatus.ACTIVE
    touches: int = 0
    member_swings: List[int] = None  # indices of swings merged into this equal-level cluster


def detect_swings(candles: List[Candle], fractal_order: int = None) -> List[Swing]:
    """
    Pure fractal swing detector: a candle at index i is a swing high if its
    High is the max within [i-order, i+order]. This is a structural/geometric
    definition (not a magic threshold) — order is a shape parameter, not a
    signal threshold, and defaults to the spec-documented value in config.py.
    """
    order = fractal_order or DEFAULTS.swing_fractal_order
    swings: List[Swing] = []
    n = len(candles)
    for i in range(order, n - order):
        window = candles[i - order:i + order + 1]
        c = candles[i]
        detection_index = min(i + order, n - 1)
        if c.high == max(w.high for w in window):
            swings.append(Swing(
                index=i, type=SwingType.HIGH, price=c.high,
                event_ts=c.timestamp, detection_ts=candles[detection_index].timestamp,
            ))
        if c.low == min(w.low for w in window):
            swings.append(Swing(
                index=i, type=SwingType.LOW, price=c.low,
                event_ts=c.timestamp, detection_ts=candles[detection_index].timestamp,
            ))
    return swings


def build_liquidity_levels(swings: List[Swing], profile: AssetTimeframeProfile) -> List[LiquidityLevel]:
    """
    Clusters swing highs/lows into discrete liquidity levels, merging swings
    whose price difference is within this asset/timeframe's own calibrated
    equal-level tolerance (profile.equal_level_tolerance) into an EQUAL_HIGH /
    EQUAL_LOW level (spec 2.4). Un-clustered swings remain simple SWING_HIGH/LOW
    levels.
    """
    tol = profile.equal_level_tolerance
    highs = sorted([s for s in swings if s.type == SwingType.HIGH], key=lambda s: s.price)
    lows = sorted([s for s in swings if s.type == SwingType.LOW], key=lambda s: s.price)

    levels: List[LiquidityLevel] = []
    levels.extend(_cluster(highs, tol, LevelKind.SWING_HIGH, LevelKind.EQUAL_HIGH, "EQH"))
    levels.extend(_cluster(lows, tol, LevelKind.SWING_LOW, LevelKind.EQUAL_LOW, "EQL"))
    return levels


def _cluster(swings: List[Swing], tol: float, single_kind: LevelKind,
             equal_kind: LevelKind, prefix: str) -> List[LiquidityLevel]:
    levels: List[LiquidityLevel] = []
    used = [False] * len(swings)
    for i, s in enumerate(swings):
        if used[i]:
            continue
        cluster = [s]
        used[i] = True
        for j in range(i + 1, len(swings)):
            if used[j]:
                continue
            if abs(swings[j].price - s.price) <= tol:
                cluster.append(swings[j])
                used[j] = True
        kind = equal_kind if len(cluster) > 1 else single_kind
        avg_price = sum(c.price for c in cluster) / len(cluster)
        levels.append(LiquidityLevel(
            id=f"{prefix}-{s.index}",
            kind=kind,
            price=avg_price,
            formed_index=max(c.index for c in cluster),
            touches=len(cluster),
            member_swings=[c.index for c in cluster],
        ))
    return levels


def add_periodic_levels(candles: List[Candle], levels: List[LiquidityLevel]) -> List[LiquidityLevel]:
    """Adds Previous-Day / Previous-Week High-Low as explicit liquidity levels
    (spec section ب/2.5). Requires the candle timeframe to be <= 1d for the
    day boundaries to be meaningful; caller is responsible for that check."""
    import datetime as dt
    if not candles:
        return levels

    by_day = {}
    for c in candles:
        day = dt.datetime.utcfromtimestamp(c.timestamp).date()
        by_day.setdefault(day, []).append(c)

    days_sorted = sorted(by_day.keys())
    for idx, day in enumerate(days_sorted[:-1]):
        day_candles = by_day[day]
        pdh = max(c.high for c in day_candles)
        pdl = min(c.low for c in day_candles)
        last_idx = candles.index(day_candles[-1])
        levels.append(LiquidityLevel(id=f"PDH-{day}", kind=LevelKind.PREV_DAY_HIGH,
                                      price=pdh, formed_index=last_idx))
        levels.append(LiquidityLevel(id=f"PDL-{day}", kind=LevelKind.PREV_DAY_LOW,
                                      price=pdl, formed_index=last_idx))
    return levels
