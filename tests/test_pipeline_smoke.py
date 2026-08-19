"""
tests/test_pipeline_smoke.py
==============================
Offline smoke test. IMPORTANT: the candles generated here are purely for
exercising code paths (loops terminate, types line up, no exceptions on a
well-formed series) — they are NEVER used as a source of trading signals or
shipped as "market data". Real signals only ever come from
data.providers.CryptoCompareProvider against the live API, per Rule #2.

Run: python -m pytest tests/ -q   (or: python tests/test_pipeline_smoke.py)
"""

import sys, os, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.providers import Candle
from data.validator import validate_candles
from engines.calibrator import calibrate
from engines.liquidity_engine import detect_swings, build_liquidity_levels
from engines.sweep_engine import scan_for_sweeps
from engines.structure_engine import confirm_structure
from models.entry_sl_tp import build_standard_signal
from config import DEFAULTS


def make_synthetic_candles(n=500, seed=42):
    random.seed(seed)
    candles = []
    price = 100.0
    ts = 1_700_000_000
    step = 3600
    for i in range(n):
        drift = math.sin(i / 15.0) * 0.5
        vol = random.uniform(0.3, 2.0)
        open_ = price
        close = open_ + drift + random.uniform(-vol, vol)
        high = max(open_, close) + random.uniform(0, vol)
        low = min(open_, close) - random.uniform(0, vol)
        volume = random.uniform(100, 1000)
        candles.append(Candle(timestamp=ts, open=open_, high=high, low=low,
                               close=close, volume=volume, closed=True))
        price = close
        ts += step
    return candles


def test_pipeline_runs_end_to_end():
    raw = make_synthetic_candles()
    candles, report = validate_candles(raw, "TEST", "1h")
    assert report.accepted
    assert len(candles) > DEFAULTS.min_lookback_candles

    profile = calibrate(candles, "TEST", "1h")
    assert profile.n_candles == len(candles)

    swings = detect_swings(candles)
    assert isinstance(swings, list)

    levels = build_liquidity_levels(swings, profile)
    assert isinstance(levels, list)

    sweeps = scan_for_sweeps(candles, levels, profile)
    for sweep in sweeps:
        conf = confirm_structure(candles, swings, sweep, profile, DEFAULTS.max_setup_age_candles)
        if conf.confirmed and conf.choch_index is not None:
            level = next(l for l in levels if l.id == sweep.level_id)
            direction = "BUY" if level.kind.name.endswith("LOW") else "SELL"
            sig = build_standard_signal(candles, sweep, conf, levels, profile, direction, rr_target=2.0)
            if sig:
                assert sig.stop_loss != sig.entry_price
                assert sig.take_profit != sig.entry_price

    print("Smoke test passed:", len(candles), "candles,", len(levels), "levels,",
          len(sweeps), "sweeps.")


if __name__ == "__main__":
    test_pipeline_runs_end_to_end()
