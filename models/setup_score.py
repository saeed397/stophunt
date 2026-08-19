"""
models/setup_score.py
======================
Implements the "Setup Score" concept from the review discussion:

    Liquidity Quality   0-25
    Sweep Quality       0-20
    Reclaim             0-15
    Structure Shift     0-20
    Volume/Order Flow   0-10
    HTF Alignment       0-10
    ----------------------------
    Total               0-100

IMPORTANT (per the source text itself): the weights above are a STARTING
STRUCTURE, explicitly flagged in the review as provisional — "این اعداد را
فعلاً نباید قطعی کنیم ... باید بعداً از Backtest و Walk-Forward Testing
استخراج شوند." This module therefore exposes the weights as a configurable
object (ScoreWeights) rather than hard-coding them as final truth, and the
Backtest Engine is expected to calibrate/replace them from real walk-forward
results before this score is trusted for live filtering.
"""

from __future__ import annotations
from dataclasses import dataclass
from engines.liquidity_engine import LiquidityLevel
from engines.sweep_engine import SweepEvent
from engines.structure_engine import StructureConfirmation


@dataclass
class ScoreWeights:
    liquidity_quality_max: float = 25
    sweep_quality_max: float = 20
    reclaim_max: float = 15
    structure_shift_max: float = 20
    volume_orderflow_max: float = 10
    htf_alignment_max: float = 10


DEFAULT_WEIGHTS = ScoreWeights()


def score_setup(level: LiquidityLevel, sweep: SweepEvent, confirmation: StructureConfirmation,
                 htf_aligned: bool, volume_percentile: float,
                 weights: ScoreWeights = DEFAULT_WEIGHTS) -> dict:
    liquidity_quality = min(weights.liquidity_quality_max, level.touches * (weights.liquidity_quality_max / 3))

    sweep_quality = 0.0
    if sweep.wick_body_ratio != float("inf"):
        sweep_quality = min(weights.sweep_quality_max, sweep.wick_body_ratio * (weights.sweep_quality_max / 3))
    else:
        sweep_quality = weights.sweep_quality_max  # pure wick, no body -> maximal rejection signature

    reclaim = weights.reclaim_max if sweep.outcome.name == "TRUE_SWEEP" else 0.0

    structure_shift = 0.0
    if confirmation.confirmed:
        structure_shift = weights.structure_shift_max * min(1.0, confirmation.displacement_strength_pctile)

    volume_orderflow = weights.volume_orderflow_max * min(1.0, max(0.0, volume_percentile))

    htf_alignment = weights.htf_alignment_max if htf_aligned else 0.0

    total = liquidity_quality + sweep_quality + reclaim + structure_shift + volume_orderflow + htf_alignment

    if total < 60:
        band = "NO TRADE"
    elif total < 75:
        band = "WEAK SETUP"
    elif total < 85:
        band = "VALID SETUP"
    else:
        band = "HIGH QUALITY SETUP"

    return {
        "liquidity_quality": round(liquidity_quality, 1),
        "sweep_quality": round(sweep_quality, 1),
        "reclaim": round(reclaim, 1),
        "structure_shift": round(structure_shift, 1),
        "volume_orderflow": round(volume_orderflow, 1),
        "htf_alignment": round(htf_alignment, 1),
        "total": round(total, 1),
        "band": band,
    }
