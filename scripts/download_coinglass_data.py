"""
Download historical derivatives data from Coinglass API v4.

Usage:
    python scripts/download_coinglass_data.py \
        --symbols BTC ETH \
        --interval 15m \
        --output user_data/data/coinglass/

Supports: funding_rate, open_interest, long_short_ratio, taker_volume, liquidation

Notes:
  - v4 aggregated endpoints ignore startTime/endTime; they return the latest N records.
  - Use --limit (max 4500) to control how many records per request.
  - Use --interval to control granularity vs time range:
      15m x 4500 = ~47 days, 1h x 4500 = ~187 days, 4h x 4500 = ~750 days
  - For broader coverage, download at 1h or 4h and forward-fill in strategy.

Endpoint strategy:
  - funding_rate, open_interest, liquidation use aggregated endpoints (symbol=BTC, no exchange)
  - long_short_ratio, taker_volume use per-exchange endpoints (exchange + symbol=BTCUSDT)
"""

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://open-api-v4.coinglass.com"

AGGREGATED_ENDPOINTS = {
    "funding_rate": "/api/futures/funding-rate/oi-weight-history",
    "open_interest": "/api/futures/open-interest/aggregated-history",
    "liquidation": "/api/futures/liquidation/aggregated-history",
}

EXCHANGE_ENDPOINTS = {
    "long_short_ratio": "/api/futures/global-long-short-account-ratio/history",
    "taker_volume": "/api/futures/taker-buy-sell-volume/history",
}


def make_request(endpoint: str, params: dict, api_key: str) -> list:
    headers = {"CG-API-KEY": api_key, "Accept": "application/json"}
    url = BASE_URL + endpoint
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            payload = resp.json()
            if str(payload.get("code")) == "0" or payload.get("success") is True:
                return payload.get("data", []) or []
            raise ValueError(f"API error (code={payload.get('code')}): {payload.get('msg', 'unknown')}")
        except requests.HTTPError:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    return []


def _download(parse_fn, endpoint: str, params_base: dict,
              api_key: str, limit: int = 4500) -> pd.DataFrame:
    params = {**params_base, "limit": limit}
    data = make_request(endpoint, params, api_key)
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(parse_fn(item) for item in data)


def download_funding_rate(symbol: str, api_key: str, interval: str,
                          limit: int = 4500) -> pd.DataFrame:
    def parse(item: dict) -> dict:
        return {
            "date": datetime.fromtimestamp(item["time"] / 1000, tz=timezone.utc),
            "funding_rate": float(item.get("close", 0)),
            "symbol": symbol,
        }
    return _download(
        parse, AGGREGATED_ENDPOINTS["funding_rate"],
        {"symbol": symbol, "interval": interval}, api_key, limit,
    )


def download_open_interest(symbol: str, api_key: str, interval: str,
                           limit: int = 4500) -> pd.DataFrame:
    def parse(item: dict) -> dict:
        return {
            "date": datetime.fromtimestamp(item["time"] / 1000, tz=timezone.utc),
            "open_interest": float(item.get("close", 0)),
            "symbol": symbol,
        }
    return _download(
        parse, AGGREGATED_ENDPOINTS["open_interest"],
        {"symbol": symbol, "interval": interval}, api_key, limit,
    )


def download_long_short_ratio(symbol: str, api_key: str, interval: str,
                              limit: int = 4500) -> pd.DataFrame:
    def parse(item: dict) -> dict:
        return {
            "date": datetime.fromtimestamp(item["time"] / 1000, tz=timezone.utc),
            "long_short_ratio": float(item.get("global_account_long_short_ratio", 0)),
            "long_account": float(item.get("global_account_long_percent", 0)),
            "short_account": float(item.get("global_account_short_percent", 0)),
            "symbol": symbol,
        }
    return _download(
        parse, EXCHANGE_ENDPOINTS["long_short_ratio"],
        {"exchange": "Binance", "symbol": f"{symbol}USDT", "interval": interval},
        api_key, limit,
    )


def download_taker_volume(symbol: str, api_key: str, interval: str,
                          limit: int = 4500) -> pd.DataFrame:
    def parse(item: dict) -> dict:
        buy = float(item.get("taker_buy_volume_usd", 0))
        sell = float(item.get("taker_sell_volume_usd", 0))
        return {
            "date": datetime.fromtimestamp(item["time"] / 1000, tz=timezone.utc),
            "taker_buy_volume": buy,
            "taker_sell_volume": sell,
            "taker_net_volume": buy - sell,
            "symbol": symbol,
        }
    return _download(
        parse, EXCHANGE_ENDPOINTS["taker_volume"],
        {"exchange": "Binance", "symbol": f"{symbol}USDT", "interval": interval},
        api_key, limit,
    )


def download_liquidation(symbol: str, api_key: str, interval: str,
                         limit: int = 4500) -> pd.DataFrame:
    def parse(item: dict) -> dict:
        long_liq = float(item.get("aggregated_long_liquidation_usd", 0))
        short_liq = float(item.get("aggregated_short_liquidation_usd", 0))
        return {
            "date": datetime.fromtimestamp(item["time"] / 1000, tz=timezone.utc),
            "liquidation_long": long_liq,
            "liquidation_short": short_liq,
            "liquidation_total": long_liq + short_liq,
            "symbol": symbol,
        }
    return _download(
        parse, AGGREGATED_ENDPOINTS["liquidation"],
        {"symbol": symbol, "interval": interval, "exchange_list": "Binance"},
        api_key, limit,
    )


DOWNLOADERS = {
    "funding_rate": download_funding_rate,
    "open_interest": download_open_interest,
    "long_short_ratio": download_long_short_ratio,
    "taker_volume": download_taker_volume,
    "liquidation": download_liquidation,
}


def main():
    parser = argparse.ArgumentParser(description="Download Coinglass derivatives data (v4)")
    parser.add_argument("--api-key", default=None,
                        help="Coinglass API key (or set COINGLASS_API_KEY env var)")
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"],
                        help="Symbols (e.g. BTC ETH)")
    parser.add_argument("--interval", default="15m", help="Time interval (15m, 1h, 4h)")
    parser.add_argument("--limit", type=int, default=4500,
                        help="Max records per request (API max: 4500)")
    parser.add_argument("--output", default="user_data/data/coinglass/",
                        help="Output directory")
    parser.add_argument("--types", nargs="+",
                        default=list(DOWNLOADERS.keys()),
                        choices=list(DOWNLOADERS.keys()),
                        help="Data types to download")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("COINGLASS_API_KEY")
    if not api_key:
        raise RuntimeError("No API key provided. Use --api-key or set COINGLASS_API_KEY env var.")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    for symbol in args.symbols:
        for data_type in args.types:
            print(f"Downloading {data_type} for {symbol} ({args.interval})...")
            try:
                df = DOWNLOADERS[data_type](
                    symbol=symbol,
                    api_key=api_key,
                    interval=args.interval,
                    limit=args.limit,
                )
            except Exception as e:
                print(f"  Error: {e}")
                continue

            if df.empty:
                print(f"  No data returned for {symbol} {data_type}")
                continue

            df = df.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
            filename = f"{symbol}_{data_type}_{args.interval}.feather"
            filepath = output_dir / filename
            df.to_feather(filepath)
            first = df["date"].min().strftime("%Y-%m-%d")
            last = df["date"].max().strftime("%Y-%m-%d")
            print(f"  Saved {len(df)} records to {filepath} ({first} ~ {last})")


if __name__ == "__main__":
    main()
