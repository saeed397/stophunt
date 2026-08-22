"""
data/providers.py
==================
Thin, honest wrappers around REAL, documented public APIs.

CHANGE LOG (this revision): CryptoCompare is no longer the OHLCV source.
Its free tier is capped at a 7-day trial, and Iran cannot purchase a paid
plan to continue past that — the user flagged this directly. Replaced with a
"Multi-Provider Strategy":

  1. Yahoo Finance (via the `yfinance` package) — PRIMARY. Free, no key,
     no 7-day trial, reachable from Iran, native fine-grained intervals.
     Ticker format for crypto is SYMBOL-QUOTE, e.g. "BTC-USD" (this is
     Yahoo Finance's own listing convention — see finance.yahoo.com/quote/BTC-USD).
     Documented interval range limits (yfinance / Yahoo chart API):
       1m            -> last 7 days only
       2m/5m/15m/30m/90m -> last 60 days only
       60m/1h        -> last 730 days
       1d/1wk/1mo    -> long range, effectively no practical limit
     Source: https://github.com/ranaroussi/yfinance and
     https://pypi.org/project/yfinance/

  2. CoinGecko OHLC endpoint — FALLBACK, used automatically when a ticker
     isn't listed on Yahoo Finance (most small/mid-cap alts) or Yahoo fails.
     Endpoint: https://docs.coingecko.com/reference/coins-id-ohlc
       GET /api/v3/coins/{id}/ohlc?vs_currency=..&days=..
     Free/demo-tier granularity is AUTOMATIC and cannot be requested directly
     (no `interval` param on the free tier):
       days 1-2   -> ~30 minute candles
       days 3-30  -> ~4 hour candles
       days 31+   -> ~4 day candles
     Because this granularity is fixed by CoinGecko (not by us), the actual
     bar duration actually returned is reported explicitly on every Candle
     batch (`actual_interval_seconds`) rather than silently assumed to match
     whatever timeframe the user picked — the calibrator and validator use
     that real value, never the nominal one, so no gap/statistic is computed
     against a wrong bar length.

  Both providers are free, key-less, and documented above — nothing here
  calls Binance (still excluded, per the original brief: not reachable from
  Iran) and nothing fabricates a candle.
"""

from __future__ import annotations
import time
import requests
from dataclasses import dataclass
from typing import List, Optional, Tuple

from config import COINGECKO_MARKETS_URL, TOP_N_ASSETS, YF_INTERVAL_LIMITS_DAYS
from utils.aggregate import aggregate_candles


class ProviderError(Exception):
    """Raised when a data provider cannot supply verifiable real data.
    Callers MUST surface this to the user rather than substituting a guess,
    per Rule #2/#3: 'اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد.'
    """


@dataclass
class Candle:
    timestamp: int          # unix seconds, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True     # an in-formation candle must never enter analysis as CLOSED


@dataclass
class OHLCVBatch:
    candles: List[Candle]
    actual_interval_seconds: int   # the REAL bar duration actually delivered — may differ from the nominal timeframe
    source: str                    # "Yahoo Finance" or "CoinGecko"
    note: Optional[str] = None     # transparency note shown to the user (e.g. fallback reason, granularity mismatch)


_NOMINAL_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
                     "1d": 86400, "1w": 604800, "1M": 2592000}


class YahooFinanceProvider:
    """Primary OHLCV source. Wraps the `yfinance` package."""

    NATIVE_INTERVAL = {
        "15m": "15m", "30m": "30m", "1h": "60m",
        "1d": "1d", "1w": "1wk", "1M": "1mo",
        # "4h" has no native Yahoo interval -> built from 60m candles, factor 4
    }

    def get_ohlcv(self, symbol: str, quote: str, timeframe: str,
                   desired_candle_count: int = 1000) -> OHLCVBatch:
        try:
            import yfinance as yf
        except ImportError as e:
            raise ProviderError(
                "کتابخانه yfinance نصب نیست (pip install yfinance). "
                "اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."
            ) from e

        ticker_symbol = f"{symbol.upper()}-{quote.upper()}"
        aggregate_factor = 1
        native_tf = timeframe
        if timeframe == "4h":
            native_tf = "1h"
            aggregate_factor = 4

        native_interval = self.NATIVE_INTERVAL.get(native_tf)
        if native_interval is None:
            raise ProviderError(f"Unsupported timeframe '{timeframe}' for Yahoo Finance.")

        max_days = YF_INTERVAL_LIMITS_DAYS.get(native_interval)
        needed_days = self._estimate_days_needed(native_interval, desired_candle_count * aggregate_factor)
        period_days = min(needed_days, max_days) if max_days else needed_days

        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(period=f"{period_days}d", interval=native_interval)
        except Exception as e:
            raise ProviderError(f"Yahoo Finance request failed for {ticker_symbol}: {e}") from e

        if df is None or df.empty:
            raise ProviderError(
                f"No Yahoo Finance data for {ticker_symbol} @ {native_interval} "
                f"(often means this coin isn't listed on Yahoo Finance). "
                f"اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."
            )

        now = int(time.time())
        native_seconds = _NOMINAL_SECONDS.get(native_tf, 3600)
        candles: List[Candle] = []
        for idx, row in df.iterrows():
            ts = int(idx.timestamp())
            is_closed = (ts + native_seconds) <= now
            candles.append(Candle(
                timestamp=ts, open=float(row["Open"]), high=float(row["High"]),
                low=float(row["Low"]), close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0)), closed=is_closed,
            ))

        actual_seconds = native_seconds
        if aggregate_factor > 1:
            candles = aggregate_candles(candles, aggregate_factor)
            actual_seconds = native_seconds * aggregate_factor

        if not candles:
            raise ProviderError(f"No usable candles for {ticker_symbol} after aggregation.")

        return OHLCVBatch(candles=candles, actual_interval_seconds=actual_seconds, source="Yahoo Finance")

    @staticmethod
    def _estimate_days_needed(interval: str, candle_count: int) -> int:
        per_day = {"15m": 96, "30m": 48, "60m": 24, "1d": 1, "1wk": 1 / 7, "1mo": 1 / 30}.get(interval, 24)
        return max(2, int(candle_count / per_day) + 2)


class CoinGeckoOHLCProvider:
    """Fallback OHLCV source for coins not listed on Yahoo Finance."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    # (max_days_to_request, resulting_native_granularity_seconds) — per CoinGecko's
    # documented automatic free-tier granularity, see module docstring above.
    GRANULARITY_TABLE: List[Tuple[int, int]] = [
        (2, 1800),        # 1-2 days   -> ~30 min candles
        (30, 14400),      # 3-30 days  -> ~4 hour candles
        (180, 345600),    # 31-180+ days -> ~4 day candles
    ]

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    def _choose_days(self, timeframe: str, desired_candle_count: int) -> Tuple[int, int]:
        """Pick the smallest `days` bucket whose native granularity is <= the
        nominal timeframe requested (finest available), falling back to the
        coarsest bucket if none matches — we never silently pretend a coarser
        granularity is the fine one the user asked for; the actual seconds
        value is always returned alongside the candles."""
        nominal = _NOMINAL_SECONDS.get(timeframe, 14400)
        best = self.GRANULARITY_TABLE[-1]
        for days, gran in self.GRANULARITY_TABLE:
            if gran <= nominal:
                best = (days, gran)
        # ensure enough days to actually cover desired_candle_count at that granularity
        days, gran = best
        needed_days = max(days, int((desired_candle_count * gran) / 86400) + 1)
        return needed_days, gran

    def get_ohlcv(self, coingecko_id: str, quote: str, timeframe: str,
                   desired_candle_count: int = 1000) -> OHLCVBatch:
        days, expected_granularity = self._choose_days(timeframe, desired_candle_count)
        url = f"{self.BASE_URL}/coins/{coingecko_id}/ohlc"
        params = {"vs_currency": quote.lower(), "days": days}

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise ProviderError(f"CoinGecko OHLC request failed: {e}") from e

        if not payload:
            raise ProviderError(
                f"اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد. "
                f"(CoinGecko OHLC returned nothing for {coingecko_id})"
            )

        now = int(time.time())
        candles: List[Candle] = []
        for row in payload:
            ts_ms, o, h, l, c = row
            ts = int(ts_ms / 1000)
            is_closed = (ts + expected_granularity) <= now
            candles.append(Candle(timestamp=ts, open=float(o), high=float(h),
                                   low=float(l), close=float(c), volume=0.0,
                                   closed=is_closed))

        # Verify actual spacing matches the documented expectation; if not,
        # trust the OBSERVED spacing (median delta) over the documented table,
        # since CoinGecko's auto-granularity behavior can change server-side.
        if len(candles) >= 3:
            deltas = sorted(candles[i + 1].timestamp - candles[i].timestamp for i in range(len(candles) - 1))
            observed = deltas[len(deltas) // 2]
            if observed > 0:
                expected_granularity = observed

        nominal = _NOMINAL_SECONDS.get(timeframe, 14400)
        note = None
        if expected_granularity != nominal:
            note = (f"CoinGecko free tier only returns ~{expected_granularity // 60}-minute candles here, "
                    f"not the requested {timeframe}. Calibration used the real bar size.")

        return OHLCVBatch(candles=candles, actual_interval_seconds=expected_granularity,
                           source="CoinGecko", note=note)


class MultiProviderOHLC:
    """
    Orchestrator implementing the requested 'Multi-Provider Strategy':
    tries Yahoo Finance first (finer, more reliable interval matching for
    majors), falls back to CoinGecko automatically for coins not listed on
    Yahoo or if Yahoo fails. Always reports which source was actually used —
    never silently swaps sources without telling the caller (Rule #2/#3).
    """

    def __init__(self):
        self.yahoo = YahooFinanceProvider()
        self.coingecko = CoinGeckoOHLCProvider()

    def get_ohlcv(self, symbol: str, coingecko_id: str, quote: str, timeframe: str,
                   desired_candle_count: int = 1000) -> OHLCVBatch:
        try:
            return self.yahoo.get_ohlcv(symbol, quote, timeframe, desired_candle_count)
        except ProviderError as yahoo_err:
            try:
                batch = self.coingecko.get_ohlcv(coingecko_id, quote, timeframe, desired_candle_count)
                fallback_note = f"Yahoo Finance در دسترس نبود ({yahoo_err}); به‌صورت خودکار از CoinGecko استفاده شد."
                batch.note = f"{fallback_note} {batch.note or ''}".strip()
                return batch
            except ProviderError as cg_err:
                raise ProviderError(
                    "هیچ‌کدام از منابع داده در دسترس نبودند — "
                    "اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد. "
                    f"[Yahoo Finance: {yahoo_err}] [CoinGecko: {cg_err}]"
                )


class CoinGeckoProvider:
    """
    Universe source (top-N assets by market cap). Endpoint reference:
    https://docs.coingecko.com/reference/coins-markets
    """

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    def get_top_assets(self, n: int = TOP_N_ASSETS) -> List[dict]:
        results = []
        per_page = 250
        pages = -(-n // per_page)
        try:
            for page in range(1, pages + 1):
                resp = self.session.get(
                    COINGECKO_MARKETS_URL,
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": per_page,
                        "page": page,
                        "sparkline": "false",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                results.extend(batch)
                if len(results) >= n:
                    break
        except Exception as e:
            raise ProviderError(f"CoinGecko request failed: {e}") from e

        if not results:
            raise ProviderError("اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد.")

        return [
            {"symbol": c["symbol"].upper(), "name": c["name"], "id": c["id"],
             "market_cap_rank": c.get("market_cap_rank")}
            for c in results[:n]
        ]

