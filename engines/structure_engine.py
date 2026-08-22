"""
engines/structure_engine.py
=============================
Implements the review framework's item #3: Sweep is not proof of reversal by
itself. This module checks for the full chain:

    Liquidity Sweep -> Reclaim -> Displacement -> MSS/CHoCH -> (confirmed)

- Reclaim: already established by SweepEvent.outcome == TRUE_SWEEP (close back
  inside the level).
- Displacement: an impulsive candle in the reversal direction whose body is
  large relative to this asset/timeframe's OWN recent body-size distribution
  (not a fixed multiple) — calibrated, per Rule #1.
- MSS/CHoCH: price subsequently breaks the most recent opposite-side internal
  swing structure, confirming a change of character.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from data.providers import Candle
from engines.liquidity_engine import Swing, SwingType
from engines.sweep_engine import SweepEvent, SweepOutcome
from engines.calibrator import AssetTimeframeProfile
from utils.indicators import percentile


@dataclass
class StructureConfirmation:
    sweep: SweepEvent
    displacement_index: Optional[int]
    displacement_strength_pctile: float   # this candle's body size vs asset's own recent distribution
    choch_index: Optional[int]
    confirmed: bool
    reason: str


def _recent_body_sizes(candles: List[Candle], upto_index: int, lookback: int = 100) -> List[float]:
    start = max(0, upto_index - lookback)
    return [abs(c.close - c.open) for c in candles[start:upto_index]]


def find_displacement(candles: List[Candle], sweep_index: int, direction_up: bool,
                       max_lookahead: int) -> Optional[int]:
    """direction_up=True means we expect a bullish displacement (sweep was on the SELL side / low)."""
    bodies_ref = _recent_body_sizes(candles, sweep_index)
    if not bodies_ref:
        return None
    for i in range(sweep_index, min(sweep_index + max_lookahead, len(candles))):
        c = candles[i]
        body = c.close - c.open
        size = abs(body)
        pct = sum(1 for b in bodies_ref if b <= size) / len(bodies_ref)
        is_directional = (body > 0) if direction_up else (body < 0)
        if is_directional and pct >= 0.75:  # top-quartile body relative to THIS asset's own recent bodies
            return i
    return None


def find_choch(candles: List[Candle], swings: List[Swing], from_index: int,
                direction_up: bool, max_lookahead: int) -> Optional[int]:
    """Looks for a break of the most recent opposite-side internal swing
    (i.e. bullish CHoCH breaks the last swing high before `from_index`)."""
    opposite_type = SwingType.HIGH if direction_up else SwingType.LOW
    candidates = [s for s in swings if s.type == opposite_type and s.index < from_index]
    if not candidates:
        return None
    last_opposite = max(candidates, key=lambda s: s.index)
    for i in range(from_index, min(from_index + max_lookahead, len(candles))):
        c = candles[i]
        if direction_up and c.close > last_opposite.price:
            return i
        if not direction_up and c.close < last_opposite.price:
            return i
    return None


def confirm_structure(candles: List[Candle], swings: List[Swing], sweep: SweepEvent,
                       profile: AssetTimeframeProfile, max_setup_age_candles: int) -> StructureConfirmation:
    if sweep.outcome != SweepOutcome.TRUE_SWEEP:
        return StructureConfirmation(sweep, None, 0.0, None, False,
                                      "Not a true sweep (breakout, not reclaimed) — no structure check performed.")

    # direction_up: sweep on the low side (sell-side liquidity) implies expected bullish reversal
    direction_up = sweep.level_id.split("-")[0] in ("EQL", "PDL")  # heuristic based on id prefix set by liquidity_engine
    # Fall back: infer from close relative to level using penetration side is already encoded upstream;
    # explicit and documented, not guessed per-candle.

    disp_idx = find_displacement(candles, sweep.candle_index, direction_up, max_setup_age_candles)
    if disp_idx is None:
        return StructureConfirmation(sweep, None, 0.0, None, False,
                                      "No qualifying displacement candle within the expiry window.")

    bodies_ref = _recent_body_sizes(candles, sweep.candle_index)
    size = abs(candles[disp_idx].close - candles[disp_idx].open)
    disp_pct = percentile(bodies_ref, 0.75) if bodies_ref else 0.0

    choch_idx = find_choch(candles, swings, disp_idx, direction_up, max_setup_age_candles)
    if choch_idx is None:
        return StructureConfirmation(sweep, disp_idx, disp_pct, None, False,
                                      "Displacement found but no confirmed CHoCH/MSS within the expiry window.")

    return StructureConfirmation(sweep, disp_idx, disp_pct, choch_idx, True,
                                  "Full chain confirmed: Sweep -> Reclaim -> Displacement -> CHoCH.")
