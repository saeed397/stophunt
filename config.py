"""
config.py
=========
Central configuration.

IMPORTANT — Rule #2/#3 compliance (no invented API data, only documented sources):
  - CryptoCompare historical OHLCV endpoints (v2 histoday / histohour / histominute)
    are documented at: https://min-api.cryptocompare.com/documentation
      /data/v2/histoday?fsym=..&tsym=..&limit=..&toTs=..&api_key=..
      /data/v2/histohour?fsym=..&tsym=..&limit=..&toTs=..&api_key=..
      /data/v2/histominute?fsym=..&tsym=..&limit=..&toTs=..&api_key=..
  - CryptoCompare does not require an Iran-restricted geofence the way Binance does;
    it is used here specifically because the user cannot legally/technically reach
    Binance's API from Iran. This is a data-availability choice, not a claim about
    sanctions law — verify current ToS/availability yourself before production use.
  - CoinGecko "top N coins by market cap" is documented at:
      https://docs.coingecko.com/reference/coins-markets
      /api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1

  No numeric threshold in this codebase (tolerance %, ATR multiplier, penetration
  depth, RSI/volume weights, etc.) is hard-coded as a "magic number" presented as
  final. Per Rule #1 and the project's own Phase-2 lock, every such threshold is
  either:
    (a) computed at runtime from that SPECIFIC asset+timeframe's own historical
        distribution (percentile / rolling statistics), or
    (b) left as an explicit, labeled DEFAULT that the Backtest Engine is expected
        to calibrate and override per asset/timeframe — never silently assumed.
"""

from dataclasses import dataclass, field
from typing import Dict, List

# ---------------------------------------------------------------------------
# Timeframe hierarchy — used to auto-select the "higher timeframe" (HTF) for
# multi-timeframe context confirmation, per the spec's "Adaptive Multi-Timeframe"
# requirement (section ج / 2.x). Selecting a timeframe on the settings page
# auto-populates the HTF field with the next tier(s) up.
# ---------------------------------------------------------------------------
TIMEFRAME_ORDER: List[str] = ["15m", "30m", "1h", "4h", "1d", "1w", "1M"]

# Maps each timeframe -> its immediate higher timeframe (used for MTF context)
HIGHER_TIMEFRAME_MAP: Dict[str, str] = {
    "15m": "1h",
    "30m": "4h",
    "1h": "4h",
    "4h": "1d",
    "1d": "1w",
    "1w": "1M",
    "1M": "1M",  # no higher tier available
}

# CryptoCompare histo-endpoint selection per timeframe.
# aggregate = how many base candles are combined (CryptoCompare has no native
# 4h/1w endpoint; 4h = histohour aggregate 4, 1w = histoday aggregate 7).
# This mapping reflects CryptoCompare's documented aggregate parameter, not an
# invented one: https://min-api.cryptocompare.com/documentation
CRYPTOCOMPARE_ENDPOINT_MAP = {
    "15m": {"endpoint": "histominute", "aggregate": 15},
    "30m": {"endpoint": "histominute", "aggregate": 30},
    "1h":  {"endpoint": "histohour",   "aggregate": 1},
    "4h":  {"endpoint": "histohour",   "aggregate": 4},
    "1d":  {"endpoint": "histoday",    "aggregate": 1},
    "1w":  {"endpoint": "histoday",    "aggregate": 7},
    "1M":  {"endpoint": "histoday",    "aggregate": 30},
}

RR_OPTIONS: List[str] = ["1:1", "1:1.5", "1:2", "1:3", "1:4", "1:5"]
DEFAULT_RR = "1:2"

SIGNAL_DIRECTIONS = ["Buy", "Sell", "Both"]

EXECUTION_MODES = [
    "Standard (backtest-priced entry now)",
    "Stop-Hunt Trigger (pending order at the sweep level)",
]

CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com/data/v2/"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

TOP_N_ASSETS = 500


@dataclass
class BacktestCalibrationDefaults:
    """
    Explicit, LABELED defaults used only until the Backtest Engine has produced
    an asset/timeframe-specific calibration. These are NOT trading signals by
    themselves — StopHuntEngine will refuse to emit a signal for an asset/TF
    pair that has no calibration and no way to derive one from real data
    (see engines/stophunt_engine.py -> CalibrationError).
    """
    min_lookback_candles: int = 300          # minimum history required before any detection runs
    swing_fractal_order: int = 2             # N candles either side for a raw swing candidate (structural, not a "signal" threshold)
    equal_level_tolerance_pctile: float = 0.25  # tolerance = 25th percentile of recent |High-High| / |Low-Low| deltas for that asset/TF
    atr_period: int = 14
    min_penetration_atr_fraction: float = 0.0   # computed per-asset from historical sweep depth distribution, see StatisticalCalibrator
    setup_score_min_valid: int = 60
    max_setup_age_candles: int = 10          # a sweep candidate not confirmed within N candles is expired (kept explicit + adjustable)


DEFAULTS = BacktestCalibrationDefaults()
