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
