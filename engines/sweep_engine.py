"""
engines/sweep_engine.py
========================
Implements spec 1's core distinction: "صرفاً عبور قیمت از یک High/Low برای من
Stop Hunt محسوب نمی‌شود" — a real Sweep requires wick-based penetration beyond
a liquidity level followed by a CLOSE back inside it (rejection), not just any
crossing. This module is intentionally independent/inspectable (spec 3.22:
"Shadow Engine باید مستقل باشد ... نه اینکه یک تابع بزرگ detect_stop_hunt()").
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from data.providers import Candle
from engines.liquidity_engine import LiquidityLevel, LevelKind, LevelStatus
from engines.calibrator import AssetTimeframeProfile


class SweepOutcome(Enum):
    TRUE_SWEEP = "TRUE_SWEEP"        # wick penetrates + close reclaims -> liquidity grab
    BREAKOUT = "BREAKOUT"            # close beyond the level -> genuine structural break, NOT a stop hunt
    NO_INTERACTION = "NO_INTERACTION"


@dataclass
class SweepEvent:
    level_id: str
    candle_index: int
    outcome: SweepOutcome
    penetration_price: float          # how far beyond the level, in price units
    penetration_atr: float            # same, normalized by that bar's ATR
    wick_body_ratio: float
    close: float


def evaluate_sweep(candles: List[Candle], level: LiquidityLevel,
                    profile: AssetTimeframeProfile, candle_index: int) -> Optional[SweepEvent]:
    c = candles[candle_index]
    a = profile.atr_series[candle_index] if candle_index < len(profile.atr_series) else 0.0
    if a <= 0:
        return None

    is_high_level = level.kind.name.endswith("HIGH")
    if is_high_level:
        touched = c.high > level.price
        if not touched:
            return None
        penetration = c.high - level.price
        closed_back_inside = c.close <= level.price
        body = abs(c.close - c.open)
        wick = c.high - max(c.open, c.close)
    else:
        touched = c.low < level.price
        if not touched:
            return None
        penetration = level.price - c.low
        closed_back_inside = c.close >= level.price
        body = abs(c.close - c.open)
        wick = min(c.open, c.close) - c.low

    wb_ratio = (wick / body) if body > 0 else float("inf")

    # A real Sweep = penetrated AND reclaimed (closed back inside) the level.
    # If price closes BEYOND the level, that is a structural Breakout, not a
    # Stop Hunt — per the user's explicit "Reclaim distinguishes them" rule.
    outcome = SweepOutcome.TRUE_SWEEP if closed_back_inside else SweepOutcome.BREAKOUT

    return SweepEvent(
        level_id=level.id,
        candle_index=candle_index,
        outcome=outcome,
        penetration_price=penetration,
        penetration_atr=penetration / a,
        wick_body_ratio=wb_ratio,
        close=c.close,
    )


def scan_for_sweeps(candles: List[Candle], levels: List[LiquidityLevel],
                     profile: AssetTimeframeProfile) -> List[SweepEvent]:
    events: List[SweepEvent] = []
    for level in levels:
        for i in range(level.formed_index + 1, len(candles)):
            ev = evaluate_sweep(candles, level, profile, i)
            if ev and ev.outcome != SweepOutcome.NO_INTERACTION:
                if ev.outcome == SweepOutcome.TRUE_SWEEP:
                    level.status = LevelStatus.SWEPT
                events.append(ev)
                break  # a level is consumed on its first meaningful interaction
    return events
