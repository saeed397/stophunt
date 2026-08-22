# Liquidity-Centric Order System (Stop Hunt Detection)

Python + Streamlit implementation of the Stop-Hunt / Liquidity Sweep strategy
specified in the source document, continuing directly from the end of
**Phase 3 (Data Architecture)**. Rather than writing more specification text
for Phase 4 ("Mathematical Formula Lock"), this repo *is* Phase 4 — every
formula is implemented in code, with the reasoning next to it, and every
number that would otherwise be an invented magic constant is instead computed
from that specific asset+timeframe's own historical data at runtime.

## How the four non-negotiable rules are enforced

| Rule (from the brief) | Where it's enforced |
|---|---|
| **#1 — every signal must be derived from that specific asset's own backtest/history in that same timeframe** | `engines/calibrator.py` computes tolerance, ATR, and penetration-depth thresholds per asset+timeframe from real candles. `engines/stophunt_engine.py`'s `run_signal_engine(asset, timeframe, ...)` cannot run without both. `backtest/backtest_engine.py` provides the actual walk-forward history. |
| **#2/#3 — no invented data/APIs; only documented, citable sources; explicit refusal message when data is missing** | `data/providers.py` only calls documented CryptoCompare and CoinGecko endpoints (URLs in `config.py` header comments). Every provider/validation failure raises with the exact Persian sentence you specified: *"اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."* Binance is not used anywhere (Iran access issue). |
| **#4 — two mandatory outputs: (a) an immediate signal priced from backtest-at-this-moment, and (b) a pending order sitting on the yet-unswept Stop-Hunt level, with SL/TP computed relative to that trigger** | `models/entry_sl_tp.py` has two dedicated functions: `build_standard_signal()` (output a) and `build_stophunt_trigger_signal()` (output b). `engines/stophunt_engine.py` always computes and returns both lists. The Streamlit UI shows them in two separate tabs. |

## Architecture (the layered pipeline you specified, not one giant condition)

```
Data Providers (CryptoCompare OHLCV, CoinGecko universe)
        ↓
Data Validator  (timestamp/duplicate/OHLC/gap checks — data/validator.py)
        ↓
Calibrator      (per-asset/TF statistical thresholds — engines/calibrator.py)
        ↓
Liquidity Engine (swings, equal-highs/lows, PDH/PDL — engines/liquidity_engine.py)
        ↓
Sweep Engine     (wick-penetration + reclaim vs. real breakout — engines/sweep_engine.py)
        ↓
Structure Engine (displacement + CHoCH/MSS confirmation — engines/structure_engine.py)
        ↓
Entry / SL / TP Models (models/entry_sl_tp.py)
        ↓
Setup Score      (0–100, provisional weights — models/setup_score.py)
        ↓
Risk Management  (position sizing, daily loss/consecutive-loss limits — risk/risk_management.py)
        ↓
Streamlit UI     (app/streamlit_app.py)
```

`backtest/backtest_engine.py` is a separate walk-forward harness (no
lookahead — it only ever looks at `candles[:i+1]` at step *i*) used to
produce the actual trade log (entry/SL/TP/MFE/MAE/outcome/duration) that
should be used to calibrate the Setup Score weights before trusting them live.

## Update 2: simplified output UI + CryptoCompare replaced (Multi-Provider Strategy)

Two changes, both scoped exactly to what was asked — nothing about the
underlying detection/calibration philosophy (Rules #1–#4) was touched.

**1. Output UI drastically simplified.** The Streamlit app no longer shows
candle counts, liquidity-level counts, a setup-score table, or raw
calibration JSON by default. It shows exactly two groups — Standard signal
and Stop-Hunt Trigger — each just three colored numbers (Entry = yellow,
Take Profit = green, Stop Loss = red) inside a bordered box. A small "ℹ️"
toggle under each box is collapsed by default; opening it reveals a short,
plain-language (no jargon) explanation of why those specific prices were
chosen — see `presentation/explain.py`. That explanation also states plainly
that the asset's own volatility/Stop-Hunt history was given priority over
the user-selected R:R ratio, and only falls back to R:R when no real
opposing liquidity level was found — exactly the priority order requested.

**2. CryptoCompare removed — "Multi-Provider Strategy" implemented.**
CryptoCompare's free tier is a 7-day trial with no way to purchase a paid
plan from Iran, so it has been fully replaced:

  - **Primary: Yahoo Finance** (via the `yfinance` package) — free, no API
    key, no trial limit, reachable from Iran, native fine-grained intervals
    (15m/30m/1h/1d/1wk/1mo; 4h is built by aggregating real 1h candles, same
    non-fabricating aggregation used previously for CryptoCompare's 4h).
  - **Fallback: CoinGecko OHLC endpoint** (`/coins/{id}/ohlc`) — used
    automatically when a coin isn't listed on Yahoo Finance or Yahoo fails.
    Its free-tier granularity is fixed/automatic (not selectable), so the
    code reports the *actual* delivered bar duration and feeds that real
    value into the validator/calibrator — never pretends a coarser candle is
    the finer one the user asked for.

Both sources and their documented parameters/limits are cited in full at the
top of `data/providers.py`. Which source actually answered a given request
is always tracked (`EngineResult.data_source`); a fallback is never silent.

## Update 1: rolling / regime-aware calibration

The original calibrator computed equal-level tolerance, wick/body ratio, and
sweep-penetration-depth statistics from an asset's **entire** lookback history.
That's Rule #1-compliant (still per-asset, per-timeframe) but not robust: a
coin that was quiet 8 months ago and is violent today would get thresholds
diluted by the old quiet period, making SL buffers too tight and TP-quality
scoring too generous right when it matters most.

`engines/calibrator.py` now computes those same statistics from only the
**most recent `rolling_calibration_window` candles** (default 300, adjustable
in the Streamlit sidebar) of that specific asset/timeframe — full history is
still used for swing/liquidity-level detection (structure needs long memory),
but SL/TP sizing tracks current behavior, not a multi-year average.

It also adds `regime_shift_flag` / `regime_shift_ratio`: if the recent
window's average ATR is ≥2x or ≤0.5x the immediately-preceding window's
average ATR, the engine surfaces a warning in the UI rather than silently
treating pre- and post-shift volatility as equivalent. This doesn't block a
signal — it flags it, so a human makes the final call on a coin that just had
a listing pump, a flash crash, or a sudden liquidity change.

## Honest limitations — read before trusting live signals

1. **This sandbox has no outbound network access**, so the code has been
   verified with an offline synthetic-candle smoke test (`tests/test_pipeline_smoke.py`,
   all logic paths execute cleanly — 500 synthetic candles → 76 liquidity
   levels → 65 sweep events → signals with distinct entry/SL/TP), but it has
   **not been run against live CryptoCompare/CoinGecko data**, since that
   requires a live internet connection. Run it yourself once deployed:
   `python tests/test_pipeline_smoke.py`, then `streamlit run app/streamlit_app.py`.
2. **The Setup Score weights (25/20/15/20/10/10) are the review discussion's
   provisional starting structure**, explicitly flagged there as needing
   Walk-Forward Backtest calibration before being trusted — this code keeps
   them provisional (`models/setup_score.py`, `ScoreWeights`), not fabricated
   as final.
3. **Fee and slippage in `risk/risk_management.py` (`RiskConfig`) are
   labeled placeholders** — you must replace them with the real numbers for
   whatever exchange you actually execute on. Nothing downstream pretends
   they're calibrated.
4. **Displacement / structure-shift thresholds use percentile rank against
   that asset's own recent candle bodies (top quartile)**, not a fixed
   multiple — this is a defensible, inspectable choice consistent with Rule
   #1, but it is a choice, not a number the source text handed us. Recalibrate
   `find_displacement()`'s `pct >= 0.75` threshold via `backtest_engine.py`
   results if you want a different sensitivity.
5. **CryptoCompare's free tier caps each request at 2000 candles** — for
   longer histories the provider needs pagination via repeated calls with
   `toTs`, which isn't implemented yet (flagged here rather than silently
   truncated data being presented as complete history).

## Not included in this zip (per your request)

GitHub repo creation/push, Streamlit Cloud connection, and the phone home-screen
shortcut are deliberately left to you, exactly as you asked. `requirements.txt`
is ready for Streamlit Cloud's standard deploy flow (point it at
`app/streamlit_app.py`).

## Run locally

```bash
pip install -r requirements.txt
python tests/test_pipeline_smoke.py     # offline sanity check
streamlit run app/streamlit_app.py      # needs real internet access
```
