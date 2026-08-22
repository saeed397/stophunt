"""
models/entry_sl_tp.py
======================
Implements review-framework items #4, #5, #6:
  - Entry: on CHoCH confirmation close, OR as a pending order at the sweep
    extreme (the "StopHunt trigger" mode required by Rule #4).
  - SL: placed beyond the sweep's extreme (the invalidation point of the
    trading hypothesis), with an ATR-based buffer derived from THIS
    asset/timeframe's own volatility — never a fixed 0.5%% / $10 style stop.
  - TP: liquidity-target based (opposite-side liquidity / next structural
    level), not a flat RR multiple in isolation — RR is reported but the
    actual price target is a real opposing liquidity level when one exists
    within the lookahead window, else falls back to the user-selected R:R.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from data.providers import Candle
from engines.sweep_engine import SweepEvent
from engines.structure_engine import StructureConfirmation
from engines.liquidity_engine import LiquidityLevel, LevelStatus
from engines.calibrator import AssetTimeframeProfile


@dataclass
class TradeSignal:
    mode: str                    # "STANDARD" or "STOPHUNT_TRIGGER"
    direction: str                # "BUY" or "SELL"
    entry_price: float
    entry_basis: str
    stop_loss: float
    sl_basis: str
    take_profit: float
    tp_basis: str
    rr_actual: float
    valid_until_index: Optional[int]  # candle index after which the setup expires (Rule/2.x expiry)


def _atr_buffer(profile: AssetTimeframeProfile, index: int, buffer_fraction: float = None) -> float:
    """
    SL buffer beyond the invalidation extreme, sized as a fraction of THIS
    asset/timeframe's own ATR at that point in time — never a fixed dollar or
    percent amount. buffer_fraction defaults to the calibrated median
    penetration depth (p50) for this asset, i.e. "big enough that a typical
    wick for this asset wouldn't have hit it by accident."
    """
    a = profile.atr_series[index] if index < len(profile.atr_series) else 0.0
    frac = buffer_fraction if buffer_fraction is not None else max(profile.penetration_depth_atr_p50, 0.1)
    return a * frac


def build_standard_signal(candles: List[Candle], sweep: SweepEvent,
                           confirmation: StructureConfirmation,
                           levels: List[LiquidityLevel],
                           profile: AssetTimeframeProfile,
                           direction: str, rr_target: float) -> Optional[TradeSignal]:
    """Signal #1 required by Rule #4: computed NOW, at analysis time, from the
    backtest history that has already played out (Sweep already happened and
    was already confirmed by structure)."""
    if not confirmation.confirmed or confirmation.choch_index is None:
        return None

    entry_idx = confirmation.choch_index
    entry_price = candles[entry_idx].close
    is_buy = direction.upper() == "BUY"

    sweep_extreme = candles[sweep.candle_index].low if is_buy else candles[sweep.candle_index].high
    buffer = _atr_buffer(profile, entry_idx)
    stop_loss = sweep_extreme - buffer if is_buy else sweep_extreme + buffer

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None

    tp_price, tp_basis = _find_liquidity_target(levels, entry_price, is_buy, entry_idx)
    if tp_price is None:
        tp_price = entry_price + risk * rr_target if is_buy else entry_price - risk * rr_target
        tp_basis = f"No qualifying opposing liquidity level found upstream; fell back to selected R:R = 1:{rr_target}"

    rr_actual = abs(tp_price - entry_price) / risk

    return TradeSignal(
        mode="STANDARD",
        direction=direction.upper(),
        entry_price=entry_price,
        entry_basis=f"Close of CHoCH confirmation candle (index {entry_idx})",
        stop_loss=stop_loss,
        sl_basis=f"Beyond sweep extreme ({sweep_extreme:.6g}) + ATR buffer ({buffer:.6g}, "
                  f"{profile.penetration_depth_atr_p50:.2f}xATR = this asset's own median historical sweep depth)",
        take_profit=tp_price,
        tp_basis=tp_basis,
        rr_actual=rr_actual,
        valid_until_index=None,
    )


def build_stophunt_trigger_signal(candles: List[Candle], level: LiquidityLevel,
                                   profile: AssetTimeframeProfile,
                                   levels: List[LiquidityLevel],
                                   direction: str, rr_target: float,
                                   current_index: int, max_age_candles: int) -> Optional[TradeSignal]:
    """
    Signal #2 required by Rule #4: a PENDING order placed AT the (not-yet-swept)
    liquidity level itself. It only activates if/when price actually reaches
    that level; SL/TP are pre-computed now relative to that trigger price,
    using this asset's own calibrated buffer and a real opposing liquidity
    target, exactly like the standard signal.
    """
    if level.status != LevelStatus.ACTIVE:
        return None

    is_buy = direction.upper() == "BUY"
    # For a BUY stop-hunt trigger we expect a sweep of a LOW-side level (sell-side liquidity)
    is_low_level = level.kind.name.endswith("LOW")
    if is_buy and not is_low_level:
        return None
    if not is_buy and is_low_level:
        return None

    trigger_price = level.price
    buffer = _atr_buffer(profile, current_index)
    stop_loss = trigger_price - buffer if is_buy else trigger_price + buffer
    risk = abs(trigger_price - stop_loss)
    if risk <= 0:
        return None

    tp_price, tp_basis = _find_liquidity_target(levels, trigger_price, is_buy, current_index)
    if tp_price is None:
        tp_price = trigger_price + risk * rr_target if is_buy else trigger_price - risk * rr_target
        tp_basis = f"No qualifying opposing liquidity level found upstream; fell back to selected R:R = 1:{rr_target}"

    rr_actual = abs(tp_price - trigger_price) / risk

    return TradeSignal(
        mode="STOPHUNT_TRIGGER",
        direction=direction.upper(),
        entry_price=trigger_price,
        entry_basis=f"Pending order AT liquidity level {level.id} ({level.kind.value}); "
                     f"activates only if price trades through this level.",
        stop_loss=stop_loss,
        sl_basis=f"Beyond trigger level + ATR buffer ({buffer:.6g}, this asset's own median sweep depth)",
        take_profit=tp_price,
        tp_basis=tp_basis,
        rr_actual=rr_actual,
        valid_until_index=current_index + max_age_candles,
    )


def _find_liquidity_target(levels: List[LiquidityLevel], from_price: float, is_buy: bool,
                            from_index: int):
    """Real, structural TP: nearest untouched opposing-side liquidity level
    beyond the entry, per review-framework item #6 (Opposite Liquidity target)."""
    candidates = [
        l for l in levels
        if l.status == LevelStatus.ACTIVE
        and l.formed_index <= from_index
        and ((is_buy and l.kind.name.endswith("HIGH") and l.price > from_price)
             or (not is_buy and l.kind.name.endswith("LOW") and l.price < from_price))
    ]
    if not candidates:
        return None, None
    nearest = min(candidates, key=lambda l: abs(l.price - from_price))
    return nearest.price, f"Nearest opposing active liquidity level: {nearest.id} ({nearest.kind.value})"
