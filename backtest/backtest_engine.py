"""
backtest/backtest_engine.py
=============================
Walk-forward backtest harness. This is what actually produces the
"سابقه معاملاتی هر رمزارز" (each asset's own trading history) that Rule #1
requires every live signal to be grounded in.

Design rules:
  - No lookahead: calibration and detection at step i only ever see
    candles[0:i+1].
  - Every closed trade is logged with the fields implied by the spec's
    trade-log section (entry, sl, tp, mfe, mae, outcome, duration).
  - Nothing here invents a fill price beyond what the OHLC of the simulated
    candle actually allows (SL/TP checked against High/Low of subsequent bars).
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional
import csv

from data.providers import Candle
from engines.calibrator import calibrate, CalibrationError
from engines.liquidity_engine import detect_swings, build_liquidity_levels, LevelStatus
from engines.sweep_engine import scan_for_sweeps, SweepOutcome
from engines.structure_engine import confirm_structure
from models.entry_sl_tp import build_standard_signal
from config import DEFAULTS


@dataclass
class TradeLogEntry:
    asset: str
    timeframe: str
    entry_index: int
    entry_time: int
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_index: Optional[int]
    exit_time: Optional[int]
    exit_price: Optional[float]
    mfe: float
    mae: float
    outcome: str          # "WIN", "LOSS", "OPEN_AT_END"
    duration_candles: Optional[int]


def _simulate_exit(candles: List[Candle], entry_index: int, direction: str,
                    stop_loss: float, take_profit: float, max_hold: int = 500):
    is_buy = direction == "BUY"
    mfe = 0.0
    mae = 0.0
    entry_price = candles[entry_index].close
    for i in range(entry_index + 1, min(entry_index + max_hold, len(candles))):
        c = candles[i]
        favorable = (c.high - entry_price) if is_buy else (entry_price - c.low)
        adverse = (entry_price - c.low) if is_buy else (c.high - entry_price)
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        hit_sl = c.low <= stop_loss if is_buy else c.high >= stop_loss
        hit_tp = c.high >= take_profit if is_buy else c.low <= take_profit
        if hit_sl and hit_tp:
            # Conservative assumption when both are touched in the same bar: SL first.
            return i, stop_loss, "LOSS", mfe, mae
        if hit_sl:
            return i, stop_loss, "LOSS", mfe, mae
        if hit_tp:
            return i, take_profit, "WIN", mfe, mae
    return None, None, "OPEN_AT_END", mfe, mae


def run_backtest(candles: List[Candle], asset: str, timeframe: str,
                  direction_filter: str, rr_target: float) -> List[TradeLogEntry]:
    trades: List[TradeLogEntry] = []
    min_hist = DEFAULTS.min_lookback_candles

    if len(candles) < min_hist + 50:
        raise CalibrationError(
            f"Only {len(candles)} candles available; need at least {min_hist + 50} "
            f"for a meaningful walk-forward backtest. اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."
        )

    step = 20  # re-calibrate every N candles instead of every single candle, for tractability
    i = min_hist
    consumed_sweep_indices = set()

    while i < len(candles):
        window = candles[:i + 1]
        try:
            profile = calibrate(window, asset, timeframe)
        except CalibrationError:
            i += step
            continue

        swings = detect_swings(window)
        levels = build_liquidity_levels(swings, profile)
        sweeps = scan_for_sweeps(window, levels, profile)
        levels_by_id = {l.id: l for l in levels}

        for sweep in sweeps:
            key = (sweep.level_id, sweep.candle_index)
            if key in consumed_sweep_indices:
                continue
            if sweep.candle_index < i - step:  # only process sweeps discovered in this fresh slice
                continue
            consumed_sweep_indices.add(key)

            level = levels_by_id.get(sweep.level_id)
            if level is None:
                continue
            conf = confirm_structure(window, swings, sweep, profile, DEFAULTS.max_setup_age_candles)
            if not conf.confirmed:
                continue

            is_low_level = level.kind.name.endswith("LOW")
            implied_direction = "BUY" if is_low_level else "SELL"
            if direction_filter != "BOTH" and implied_direction != direction_filter:
                continue

            sig = build_standard_signal(window, sweep, conf, levels, profile, implied_direction, rr_target)
            if not sig or conf.choch_index is None:
                continue

            exit_idx, exit_price, outcome, mfe, mae = _simulate_exit(
                candles, conf.choch_index, implied_direction, sig.stop_loss, sig.take_profit
            )

            trades.append(TradeLogEntry(
                asset=asset, timeframe=timeframe,
                entry_index=conf.choch_index, entry_time=candles[conf.choch_index].timestamp,
                direction=implied_direction, entry_price=sig.entry_price,
                stop_loss=sig.stop_loss, take_profit=sig.take_profit,
                exit_index=exit_idx, exit_time=candles[exit_idx].timestamp if exit_idx else None,
                exit_price=exit_price, mfe=mfe, mae=mae, outcome=outcome,
                duration_candles=(exit_idx - conf.choch_index) if exit_idx else None,
            ))
        i += step

    return trades


def write_trade_log_csv(trades: List[TradeLogEntry], path: str):
    if not trades:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
        writer.writeheader()
        for t in trades:
            writer.writerow(asdict(t))


def summarize(trades: List[TradeLogEntry]) -> dict:
    closed = [t for t in trades if t.outcome in ("WIN", "LOSS")]
    wins = [t for t in closed if t.outcome == "WIN"]
    total = len(closed)
    win_rate = (len(wins) / total * 100) if total else 0.0
    avg_r = None
    if closed:
        rs = []
        for t in closed:
            risk = abs(t.entry_price - t.stop_loss)
            if risk <= 0:
                continue
            realized = (t.exit_price - t.entry_price) if t.direction == "BUY" else (t.entry_price - t.exit_price)
            rs.append(realized / risk)
        avg_r = sum(rs) / len(rs) if rs else None
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate_pct": round(win_rate, 2),
        "avg_r_multiple": round(avg_r, 3) if avg_r is not None else None,
    }
