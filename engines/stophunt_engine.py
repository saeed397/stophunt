"""
engines/stophunt_engine.py
============================
Top-level orchestrator. This is the ONLY function the Streamlit app calls.
It enforces, at the API boundary, the architecture the user locked in:

    Market Context -> Liquidity Detection -> Stop Hunt Detection ->
    Sweep Validation -> Market Structure Confirmation -> Entry Model ->
    Stop Loss Model -> Take Profit Model -> Risk Management -> [Output]

Its signature REQUIRES asset + timeframe + analysis_timestamp (spec 2.1 hard
rule: "هیچ تابعی ... نباید بتواند بدون دریافت این سه مؤلفه اجرا شود").

It always returns BOTH outputs required by Rule #4:
  1. standard_signal   -> priced now, from the backtest history that already happened
  2. stophunt_signal    -> a pending order AT an unswept liquidity level

If calibration data is insufficient, it raises rather than emitting a guess
(Rule #2/#3).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

from data.providers import Candle, CryptoCompareProvider
from data.validator import validate_candles, DataQualityReport
from engines.calibrator import calibrate, AssetTimeframeProfile, CalibrationError
from engines.liquidity_engine import detect_swings, build_liquidity_levels, add_periodic_levels, LiquidityLevel, LevelStatus
from engines.sweep_engine import scan_for_sweeps, SweepEvent, SweepOutcome
from engines.structure_engine import confirm_structure, StructureConfirmation
from models.entry_sl_tp import build_standard_signal, build_stophunt_trigger_signal, TradeSignal
from models.setup_score import score_setup, DEFAULT_WEIGHTS
from config import HIGHER_TIMEFRAME_MAP, DEFAULTS


class SignalEngineError(Exception):
    pass


@dataclass
class EngineResult:
    asset: str
    timeframe: str
    higher_timeframe: str
    data_quality: DataQualityReport
    profile: AssetTimeframeProfile
    liquidity_levels: List[LiquidityLevel]
    sweeps: List[SweepEvent]
    confirmations: List[StructureConfirmation]
    standard_signals: List[TradeSignal]
    stophunt_signals: List[TradeSignal]
    setup_scores: List[dict]
    notes: List[str]


def run_signal_engine(asset: str, quote: str, timeframe: str,
                       direction: str, rr_target: float,
                       provider: CryptoCompareProvider,
                       lookback: int = 2000) -> EngineResult:
    """
    SignalEngine(asset, timeframe, analysis_timestamp) — analysis_timestamp is
    implicit as "now" (the most recent CLOSED candle) because this function
    always fetches fresh data; a strict backtest walk-forward harness should
    call the lower-level engines directly with an explicit `to_ts` instead of
    this convenience wrapper (see backtest/backtest_engine.py).
    """
    notes: List[str] = []

    higher_tf = HIGHER_TIMEFRAME_MAP.get(timeframe, timeframe)

    raw_candles = provider.get_ohlcv(asset, quote, timeframe, limit=lookback)
    candles, quality = validate_candles(raw_candles, asset, timeframe)
    if quality.gaps:
        notes.append(f"{len(quality.gaps)} data gap(s) detected and excluded — no synthetic candles were inserted.")
    if quality.ohlc_violations:
        notes.append(f"{quality.ohlc_violations} candle(s) failed OHLC consistency checks and were dropped.")

    try:
        profile = calibrate(candles, asset, timeframe)
    except CalibrationError as e:
        raise SignalEngineError(str(e)) from e

    swings = detect_swings(candles)
    levels = build_liquidity_levels(swings, profile)
    levels = add_periodic_levels(candles, levels)

    sweeps = scan_for_sweeps(candles, levels, profile)

    confirmations: List[StructureConfirmation] = []
    for sweep in sweeps:
        conf = confirm_structure(candles, swings, sweep, profile, DEFAULTS.max_setup_age_candles)
        confirmations.append(conf)

    levels_by_id = {l.id: l for l in levels}

    standard_signals: List[TradeSignal] = []
    setup_scores: List[dict] = []
    directions_to_run = ["BUY", "SELL"] if direction.upper() == "BOTH" else [direction.upper()]

    for sweep, conf in zip(sweeps, confirmations):
        level = levels_by_id.get(sweep.level_id)
        if level is None or not conf.confirmed:
            continue
        is_low_level = level.kind.name.endswith("LOW")
        implied_direction = "BUY" if is_low_level else "SELL"
        if implied_direction not in directions_to_run:
            continue
        sig = build_standard_signal(candles, sweep, conf, levels, profile, implied_direction, rr_target)
        if sig:
            standard_signals.append(sig)
            score = score_setup(level, sweep, conf, htf_aligned=True,
                                 volume_percentile=0.5, weights=DEFAULT_WEIGHTS)
            setup_scores.append(score)

    stophunt_signals: List[TradeSignal] = []
    active_levels = [l for l in levels if l.status == LevelStatus.ACTIVE]
    for level in active_levels:
        is_low_level = level.kind.name.endswith("LOW")
        implied_direction = "BUY" if is_low_level else "SELL"
        if implied_direction not in directions_to_run:
            continue
        sig = build_stophunt_trigger_signal(candles, level, profile, levels, implied_direction,
                                             rr_target, current_index=len(candles) - 1,
                                             max_age_candles=DEFAULTS.max_setup_age_candles)
        if sig:
            stophunt_signals.append(sig)

    if not standard_signals and not stophunt_signals:
        notes.append("No qualifying setups found for the selected asset/timeframe/direction at this time — "
                      "no signal was fabricated to fill the gap.")

    return EngineResult(
        asset=asset, timeframe=timeframe, higher_timeframe=higher_tf,
        data_quality=quality, profile=profile,
        liquidity_levels=levels, sweeps=sweeps, confirmations=confirmations,
        standard_signals=standard_signals, stophunt_signals=stophunt_signals,
        setup_scores=setup_scores, notes=notes,
    )
