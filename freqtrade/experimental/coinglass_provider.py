"""
Coinglass data provider for FreqAI strategies.

Loads Coinglass feather files and optionally fetches real-time data
from the Coinglass v4 API during live/dry-run mode.

Features:
  - Feather file cache with automatic staleness detection
  - Real-time API fetch when cache is stale (> interval age)
  - Fallback chain: requested interval -> 15m -> 1h -> 4h
"""

import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("user_data/data/coinglass")
FALLBACK_INTERVALS = ["15m", "1h", "4h"]
DATA_TYPE_PREFIXES = (
    "funding_rate", "open_interest", "long_short_ratio",
    "taker_volume", "liquidation",
)

_STALE_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400}

_ENDPOINTS = {
    "funding_rate": "/api/futures/funding-rate/history",
    "open_interest": "/api/futures/open-interest/aggregated-history",
    "taker_volume": "/api/futures/taker-buy-sell-volume/history",
    "liquidation": "/api/futures/liquidation/history",
    "long_short_ratio": "/api/futures/global-long-short-account-ratio/history",
}

_BASE_URL = "https://open-api-v4.coinglass.com"
_REQUEST_TIMEOUT = 30
_FETCH_LIMIT = 500


class CoinglassProvider:
    """Load and merge Coinglass derivatives data into strategy dataframes."""

    def __init__(self, data_dir: str | Path | None = None, api_key: str | None = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._cache: dict[str, pd.DataFrame] = {}
        self._cache_ts: dict[str, float] = {}
        self.api_key = api_key or os.environ.get("COINGLASS_API_KEY")
        if self.api_key:
            logger.info("CoinglassProvider: real-time fetch enabled")
        else:
            logger.info("CoinglassProvider: using cached feather files only")

    def _load(self, symbol: str, data_type: str, interval: str = "15m") -> pd.DataFrame:
        key = f"{symbol}_{data_type}_{interval}"
        now = time.time()
        stale_limit = _STALE_SECONDS.get(interval, 3600)

        if key in self._cache:
            if now - self._cache_ts.get(key, 0) < stale_limit:
                return self._cache[key]

        filepath = self.data_dir / f"{key}.feather"
        df = pd.DataFrame()
        if filepath.exists():
            df = pd.read_feather(filepath)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], utc=True)
                df = df.set_index("date")

        is_stale = False
        if not df.empty and self.api_key:
            last_ts = df.index.max()
            if hasattr(last_ts, "timestamp"):
                age = now - last_ts.timestamp()
                is_stale = age > stale_limit

        if is_stale and self.api_key:
            live_df = self._fetch_live(symbol, data_type, interval)
            if not live_df.empty:
                self._cache[key] = live_df
                self._cache_ts[key] = now
                return live_df

        if not df.empty:
            self._cache[key] = df
            self._cache_ts[key] = now
            return df

        if key in self._cache:
            return self._cache[key]

        return pd.DataFrame()

    def _fetch_live(
        self, symbol: str, data_type: str, interval: str, limit: int = _FETCH_LIMIT,
    ) -> pd.DataFrame:
        endpoint = _ENDPOINTS.get(data_type)
        if not endpoint:
            return pd.DataFrame()

        headers = {"CG-API-KEY": self.api_key, "Accept": "application/json"}
        params = {"symbol": symbol, "interval": interval, "limit": limit}

        # Some endpoints require exchange parameter with BTCUSDT symbol format
        if data_type in ("funding_rate", "taker_volume", "liquidation", "long_short_ratio"):
            params["exchange"] = "Binance"
            params["symbol"] = f"{symbol}USDT"

        try:
            resp = requests.get(
                f"{_BASE_URL}{endpoint}", headers=headers,
                params=params, timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                logger.warning("Coinglass rate limited for %s/%s", symbol, data_type)
                return pd.DataFrame()

            resp.raise_for_status()
            payload = resp.json()

            if not (payload.get("success") is True or str(payload.get("code")) == "0"):
                logger.warning("Coinglass API error for %s/%s: %s",
                               symbol, data_type, payload.get("msg", "unknown"))
                return pd.DataFrame()

            data = payload.get("data", [])
            if not data:
                return pd.DataFrame()

            df = pd.DataFrame(data)
            if "time" in df.columns:
                df["date"] = pd.to_datetime(df["time"], unit="ms", utc=True)
                df = df.drop(columns=["time"])
            elif "createTime" in df.columns:
                df["date"] = pd.to_datetime(df["createTime"], unit="ms", utc=True)
                df = df.drop(columns=["createTime"])

            if "date" in df.columns:
                df = df.set_index("date").sort_index()

            if not df.empty:
                self._persist_cache(symbol, data_type, interval, df)
                logger.info("Coinglass live fetch: %s/%s/%s, %d rows",
                            symbol, data_type, interval, len(df))

            return df

        except Exception as e:
            logger.warning("Coinglass fetch failed for %s/%s: %s", symbol, data_type, e)
            return pd.DataFrame()

    def _persist_cache(
        self, symbol: str, data_type: str, interval: str, df: pd.DataFrame,
    ) -> None:
        filepath = self.data_dir / f"{symbol}_{data_type}_{interval}.feather"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            save_df = df.copy().reset_index()
            if "date" in save_df.columns:
                save_df["date"] = save_df["date"].dt.strftime("%Y-%m-%d %H:%M:%S")
            save_df.to_feather(filepath)
        except Exception as e:
            logger.warning("Failed to persist Coinglass cache: %s", e)

    def _resolve_interval(self, symbol: str, data_type: str, preferred: str) -> str | None:
        if not self._load(symbol, data_type, preferred).empty:
            return preferred
        for fallback in FALLBACK_INTERVALS:
            if fallback != preferred and not self._load(symbol, data_type, fallback).empty:
                logger.info("Coinglass %s/%s: %s not available, using %s",
                            symbol, data_type, preferred, fallback)
                return fallback
        return None

    def get_features(
        self,
        symbol: str,
        interval: str = "15m",
        data_types: tuple[str, ...] = (
            "funding_rate",
            "open_interest",
            "long_short_ratio",
            "taker_volume",
            "liquidation",
        ),
    ) -> pd.DataFrame:
        frames = []
        for dt in data_types:
            resolved = self._resolve_interval(symbol, dt, interval)
            if resolved is None:
                logger.warning("Coinglass data not found: %s/%s (tried %s)",
                               symbol, dt, [interval] + FALLBACK_INTERVALS)
                continue
            df = self._load(symbol, dt, resolved)
            if df.empty:
                continue
            cols = [c for c in df.columns if c != "symbol"]
            df = df[cols].rename(columns={c: f"{dt}_{c}" for c in cols})
            frames.append(df)

        if not frames:
            return pd.DataFrame()

        result = frames[0]
        for f in frames[1:]:
            result = result.join(f, how="outer")

        return result.sort_index()

    def merge_into(
        self,
        dataframe: pd.DataFrame,
        symbol: str,
        interval: str = "15m",
    ) -> pd.DataFrame:
        features = self.get_features(symbol, interval)
        if features.empty:
            logger.warning("No Coinglass features available for %s", symbol)
            return dataframe

        if "date" not in dataframe.columns:
            return dataframe

        merge_df = dataframe.copy()
        merge_df["_date"] = pd.to_datetime(merge_df["date"], utc=True)

        features_indexed = features.copy()
        features_indexed.index = pd.to_datetime(features_indexed.index, utc=True)

        merged = merge_df.merge(
            features_indexed,
            left_on="_date",
            right_index=True,
            how="left",
        )
        merged = merged.drop(columns=["_date"])
        merged = merged.sort_values("date").reset_index(drop=True)

        coinglass_cols = [c for c in merged.columns
                          if any(c.startswith(f"{p}_") for p in DATA_TYPE_PREFIXES)]
        merged[coinglass_cols] = merged[coinglass_cols].ffill().bfill()

        return merged
