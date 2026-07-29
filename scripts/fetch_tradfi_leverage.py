#!/usr/bin/env python3
"""拉取币安合约杠杆阶梯表 (GET /fapi/v1/leverageBracket, 需签名)。

用法:
    .venv/bin/python scripts/fetch_tradfi_leverage.py [SYMBOL ...]

无参数时拉取 configs/backtest/TradFi.json 白名单全部合约 + BTCDOM/ALL 指数。
API key 从 user_data/configs/secrets.local.json 读取 (exchange.key/secret),
结果写 user_data/data/tradfi_leverage_brackets.json。
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
from pathlib import Path

import requests

BASE = "https://fapi.binance.com"
ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "user_data/configs/secrets.local.json"
OUT = ROOT / "user_data/data/tradfi_leverage_brackets.json"
CONFIG = ROOT / "user_data/configs/backtest/TradFi.json"
EXTRA_INDEX = ["BTCDOMUSDT", "ALLUSDT"]


def load_symbols() -> list[str]:
    if len(sys.argv) > 1:
        return [s.upper() for s in sys.argv[1:]]
    cfg = json.loads(CONFIG.read_text())
    pairs = cfg["exchange"]["pair_whitelist"]
    return [p.split("/")[0] + "USDT" for p in pairs] + EXTRA_INDEX


def main() -> int:
    creds = json.loads(SECRETS.read_text())["exchange"]
    key, secret = creds.get("key", ""), creds.get("secret", "")
    if not key or not secret:
        print("ERROR: secrets.local.json 中 exchange.key/secret 为空, 请先填入只读 API key")
        return 1

    sess = requests.Session()
    sess.headers["X-MBX-APIKEY"] = key
    result = {}
    for sym in load_symbols():
        qs = urllib.parse.urlencode({"symbol": sym, "timestamp": int(time.time() * 1000)})
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        r = sess.get(f"{BASE}/fapi/v1/leverageBracket?{qs}&signature={sig}", timeout=30)
        if r.status_code != 200:
            print(f"{sym}: HTTP {r.status_code} {r.text[:120]}")
            continue
        brackets = r.json()[0]["brackets"]
        result[sym] = {
            "max_leverage": brackets[0]["initialLeverage"],
            "tiers": [
                {
                    "notional_cap": b["notionalCap"],
                    "max_leverage": b["initialLeverage"],
                    "maint_margin_ratio": b["maintMarginRatio"],
                }
                for b in brackets
            ],
        }
        print(f"{sym:14s} max_leverage={brackets[0]['initialLeverage']:>3}x  tiers={len(brackets)}")
        time.sleep(0.2)

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
