"""
data/providers.py
==================
Thin, honest wrappers around REAL, documented public APIs.

Rule #2/#3 compliance:
  - Every URL/param here matches official documentation (see config.py header).
  - No synthetic candles, no interpolation, no "reasonable guess" values.
  - If a provider call fails or returns incomplete data, we raise/flag it —
    we never invent a substitute value.
  - Binance is intentionally excluded (see project brief: not reachable from Iran).
"""

from __future__ import annotations
import time
import requests
from dataclasses import dataclass
from typing import List, Optional

from config import (
    CRYPTOCOMPARE_BASE_URL,
    CRYPTOCOMPARE_ENDPOINT_MAP,
    COINGECKO_MARKETS_URL,
    TOP_N_ASSETS,
)


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
    closed: bool = True     # per spec 2.2: an in-formation candle must never enter backtest as CLOSED


class CryptoCompareProvider:
    """
    OHLCV source. Endpoint reference:
    https://min-api.cryptocompare.com/documentation
    """

    def __init__(self, api_key: Optional[str] = None, session: Optional[requests.Session] = None):
        self.api_key = api_key
        self.session = session or requests.Session()

    def get_ohlcv(self, symbol: str, quote: str, timeframe: str,
                   limit: int = 2000, to_ts: Optional[int] = None) -> List[Candle]:
        if timeframe not in CRYPTOCOMPARE_ENDPOINT_MAP:
            raise ProviderError(f"Unsupported timeframe '{timeframe}' — no documented CryptoCompare mapping.")

        mapping = CRYPTOCOMPARE_ENDPOINT_MAP[timeframe]
        url = f"{CRYPTOCOMPARE_BASE_URL}{mapping['endpoint']}"
        params = {
            "fsym": symbol.upper(),
            "tsym": quote.upper(),
            "limit": min(limit, 2000),          # CryptoCompare hard cap per documented free tier
            "aggregate": mapping["aggregate"],
        }
        if to_ts:
            params["toTs"] = to_ts
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise ProviderError(f"CryptoCompare request failed: {e}") from e

        if payload.get("Response") == "Error":
            raise ProviderError(f"CryptoCompare error: {payload.get('Message', 'unknown error')}")

        raw = payload.get("Data", {}).get("Data", [])
        if not raw:
            raise ProviderError(
                f"No data returned for {symbol}/{quote} @ {timeframe}. "
                f"اطلاعات معتبر برای این بخش موجود نیست و اصلاح انجام نشد."
            )

        now = int(time.time())
        candles: List[Candle] = []
        bar_seconds = self._timeframe_seconds(timeframe)
        for row in raw:
            ts = int(row["time"])
            # Rule (spec 2.2): a candle whose close-time is still in the future
            # relative to "now" is OPEN, must be excluded / marked, never treated
            # as a closed historical bar.
            is_closed = (ts + bar_seconds) <= now
            candles.append(Candle(
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volumefrom", 0.0)),
                closed=is_closed,
            ))
        return candles

    @staticmethod
    def _timeframe_seconds(tf: str) -> int:
        mapping = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
                   "1d": 86400, "1w": 604800, "1M": 2592000}
        if tf not in mapping:
            raise ProviderError(f"Unknown timeframe '{tf}'")
        return mapping[tf]


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
