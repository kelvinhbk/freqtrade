"""
自动选币脚本：从 Binance 现货中选出低相关、适合超卖反弹策略的交易对。
用法: python scripts/select_pairs.py
"""

import ccxt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

# --- 配置 ---
EXCHANGE = "binance"
QUOTE = "USDT"
MIN_DAILY_VOLUME_USDT = 5_000_000  # 日均成交额下限
MIN_PRICE_USDT = 0.01  # 最低价格
MAX_PRICE_USDT = 100_000  # 最高价格
MIN_DAILY_VOLATILITY = 0.03  # 日均波动率下限（3%），超卖反弹策略需要足够波动
MIN_LISTING_DAYS = 60  # 上线天数下限
CORRELATION_DAYS = 30
MAX_CORRELATION = 0.7
TARGET_PAIR_COUNT = 15
MIN_SECTORS = 3

# 排除列表：稳定币、杠杆代币、已退市/死亡币
EXCLUDED_BASES = {
    # 稳定币
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "UST", "USD1",
    "FRAX", "LUSD", "USDE", "PYUSD",
    # 杠杆代币
    "ETHBULL", "ETHBEAR", "BNBBULL", "BNBBEAR", "BTCDOM",
    "DEFI", "BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN",
    # 已退市/合并/死亡币
    "NPXS", "VEN", "LEND", "BCC", "GXS", "HC", "BCHABC", "BCHSV",
    "YOYO", "COS", "FIRO", "WRX", "CTV", "ALPACA", "LIT",
    "HNT", "MOB", "AGIX", "NOM", "KAT", "TRX", "RNDR",
    "FTT", "LUNA", "LUNC",
    # 已迁移代币
    "MATIC", "POL",
}

# 已知板块映射
SECTOR_MAP = {
    # Layer2
    "OP": "Layer2", "ARB": "Layer2",
    "APT": "Layer2", "SEI": "Layer2", "METIS": "Layer2", "IMX": "Layer2",
    "STRK": "Layer2", "SKL": "Layer2", "LRC": "Layer2", "CELO": "Layer2",
    "MANTA": "Layer2", "BLUR": "Layer2", "MANTA": "Layer2",
    # DeFi
    "UNI": "DeFi", "AAVE": "DeFi", "LINK": "DeFi", "COMP": "DeFi",
    "MKR": "DeFi", "CRV": "DeFi", "SUSHI": "DeFi", "1INCH": "DeFi",
    "SNX": "DeFi", "DYDX": "DeFi", "LDO": "DeFi", "JUP": "DeFi",
    "RUNE": "DeFi", "INJ": "DeFi", "CAKE": "DeFi", "PENDLE": "DeFi",
    "ENA": "DeFi", "JTO": "DeFi",
    # Meme
    "DOGE": "Meme", "SHIB": "Meme", "FLOKI": "Meme", "PEPE": "Meme",
    "BONK": "Meme", "WIF": "Meme", "BOME": "Meme", "MEME": "Meme",
    "TRUMP": "Meme",
    # AI
    "FET": "AI", "RENDER": "AI", "TAO": "AI", "WLD": "AI",
    "AKT": "AI", "OCEAN": "AI", "AR": "AI",
    # 新公链 / L1
    "SOL": "L1", "ADA": "L1", "AVAX": "L1", "NEAR": "L1",
    "DOT": "L1", "ATOM": "L1", "ALGO": "L1", "EOS": "L1",
    "FTM": "L1", "ICP": "L1", "KAVA": "L1",
    "SUI": "L1", "TIA": "L1", "TON": "L1", "KAS": "L1",
    "MINA": "L1", "MOVE": "L1", "BERA": "L1",
    # BTC 生态
    "STX": "BTC_Ecosystem", "ORDI": "BTC_Ecosystem", "SATS": "BTC_Ecosystem",
    # 隐私
    "XMR": "Privacy", "ZEC": "Privacy",
    # 游戏 / NFT
    "AXS": "Gaming", "SAND": "Gaming", "MANA": "Gaming", "GALA": "Gaming",
    "ENJ": "Gaming", "MAGIC": "Gaming", "PORTAL": "Gaming",
    # 存储
    "FIL": "Storage",
    # RWA
    "ONDO": "RWA",
    # 交易所平台币
    "BNB": "Exchange", "OKB": "Exchange", "CRO": "Exchange", "GT": "Exchange",
    # 支付
    "XRP": "Payments", "XLM": "Payments", "HBAR": "Payments",
    # 供应链
    "VET": "SupplyChain",
    # 其他
    "ETH": "SmartContract", "BTC": "StoreOfValue",
    "XTZ": "L1", "THETA": "Streaming",
    "CFX": "L1", "APE": "Gaming", "WOO": "Exchange",
    "GRT": "Infrastructure", "API3": "Infrastructure",
    "EIGEN": "Infrastructure", "TIA": "ModularDA",
}


def fetch_pairs_and_data():
    """从 Binance 获取所有 USDT 现货对的日线数据。"""
    exchange = ccxt.binance({"enableRateLimit": True})
    markets = exchange.load_markets()

    usdt_pairs = [
        symbol for symbol, m in markets.items()
        if m["quote"] == QUOTE and m["spot"] and not m.get("linear")
        and m.get("active", False)
    ]
    usdt_pairs = [
        s for s in usdt_pairs
        if s.replace("/USDT", "") not in EXCLUDED_BASES
    ]
    print(f"Binance USDT 现货对（活跃+排除后）: {len(usdt_pairs)}")

    all_data = {}
    for i, symbol in enumerate(usdt_pairs):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, "1d", limit=CORRELATION_DAYS + 10)
            if len(ohlcv) < CORRELATION_DAYS + 5:
                continue
            df = pd.DataFrame(ohlcv, columns=["ts", "o", "h", "l", "c", "v"])
            latest_price = df["c"].iloc[-1]
            if latest_price < MIN_PRICE_USDT or latest_price > MAX_PRICE_USDT:
                continue
            avg_daily_volume = df["v"].mean() * df["c"].mean()
            if avg_daily_volume < MIN_DAILY_VOLUME_USDT:
                continue

            # 计算日均波动率 (ATR/close)
            df["tr"] = np.maximum(
                df["h"] - df["l"],
                np.maximum(
                    abs(df["h"] - df["c"].shift(1)),
                    abs(df["l"] - df["c"].shift(1)),
                ),
            )
            avg_volatility = (df["tr"] / df["c"]).mean()
            if avg_volatility < MIN_DAILY_VOLATILITY:
                continue

            # 检查上线天数（用最早的 ts 判断）
            # ccxt 的 fetch_ohlcv 只返回 limit 条数据，我们用数据起始时间估算
            first_ts = df["ts"].iloc[0]
            listing_days = (df["ts"].iloc[-1] - first_ts) / (1000 * 86400)
            # 如果拿到的数据不到 CORRELATION_DAYS+5 天，说明上线时间可能不够
            # 但 ccxt limit 参数限制了返回条数，所以我们多取一些来估算
            # 实际上这里 listing_days 只是数据长度，不是真正上线时间
            # 跳过此检查，改用更长的历史数据获取

            all_data[symbol] = {
                "close": df["c"].values[-CORRELATION_DAYS:],
                "returns": df["c"].pct_change().dropna().values[-CORRELATION_DAYS:],
                "avg_volume": avg_daily_volume,
                "avg_volatility": avg_volatility,
            }
        except Exception:
            continue
        if (i + 1) % 50 == 0:
            print(f"  已处理 {i + 1}/{len(usdt_pairs)} ...")

    print(f"符合条件（流动性+波动率+数据完整）: {len(all_data)} 个")
    return all_data


def check_listing_age(all_data: dict) -> dict:
    """检查币对上线时间是否足够长（用于回测数据充足性）。"""
    exchange = ccxt.binance({"enableRateLimit": True})
    filtered = {}
    for symbol in list(all_data.keys()):
        try:
            # 获取最早的可用数据来估算上线时间
            ohlcv = exchange.fetch_ohlcv(symbol, "1d", limit=500)
            if len(ohlcv) < MIN_LISTING_DAYS:
                continue
            filtered[symbol] = all_data[symbol]
        except Exception:
            continue
    print(f"上线 >= {MIN_LISTING_DAYS} 天: {len(filtered)} 个")
    return filtered


def assign_sector(base: str) -> str:
    """给币种分配板块。"""
    if base in SECTOR_MAP:
        return SECTOR_MAP[base]
    return "Unknown"


def select_pairs(all_data: dict) -> tuple:
    """核心选币逻辑：策略适配性 > 相关性 > 板块分散。"""
    # 构建收益率矩阵
    symbols = list(all_data.keys())
    returns_data = {s: all_data[s]["returns"] for s in symbols}
    returns_df = pd.DataFrame(returns_data).dropna()

    # 计算相关系数矩阵
    corr_matrix = returns_df.corr()

    # 板块归类
    bases = [s.replace("/USDT", "") for s in symbols]
    sector_map = {}
    for base in bases:
        sector = assign_sector(base)
        if sector != "Unknown":
            sector_map[base] = sector

    # 排除不适合的板块
    excluded_sectors = {"Exchange", "StoreOfValue", "SmartContract"}

    # 收集所有候选（已知板块 + 排除特殊板块）
    candidates = []
    for symbol in symbols:
        base = symbol.replace("/USDT", "")
        if base not in sector_map:
            continue
        sector = sector_map[base]
        if sector in excluded_sectors:
            continue
        candidates.append({
            "symbol": symbol,
            "base": base,
            "sector": sector,
            "avg_volume": all_data[symbol]["avg_volume"],
            "avg_volatility": all_data[symbol]["avg_volatility"],
        })

    # 综合评分：波动率(策略适配) * 成交额(流动性)
    # 波动率在 3%-10% 区间得分最高，太低不好触发信号，太高风险大
    for c in candidates:
        vol = c["avg_volatility"]
        # 波动率得分：5%左右最优，用高斯衰减
        vol_score = np.exp(-((vol - 0.06) ** 2) / (2 * 0.04**2))
        # 成交额得分：取 log 缩放
        vol_log = np.log10(c["avg_volume"] + 1)
        c["score"] = vol_score * vol_log

    # 按综合评分排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 打印候选列表
    print(f"\n候选币对（共 {len(candidates)} 个，按适配性排序）:")
    print(f"  {'排名':>4s}  {'币对':15s}  {'板块':15s}  {'波动率':>6s}  {'成交额M':>8s}  {'评分':>6s}")
    for i, c in enumerate(candidates[:30]):
        print(
            f"  {i+1:4d}  {c['symbol']:15s}  {c['sector']:15s}  "
            f"{c['avg_volatility']*100:5.1f}%  {c['avg_volume']/1e6:7.1f}M  {c['score']:.3f}"
        )

    # 贪心选币：按评分从高到低，加入低相关币
    selected = []
    for cand in candidates:
        sym = cand["symbol"]
        too_correlated = False
        for sel in selected:
            sel_sym = sel["symbol"]
            if sel_sym in corr_matrix.columns and sym in corr_matrix.columns:
                corr_val = abs(corr_matrix.loc[sel_sym, sym])
                if corr_val > MAX_CORRELATION:
                    too_correlated = True
                    break
        if not too_correlated:
            selected.append(cand)
        if len(selected) >= TARGET_PAIR_COUNT:
            break

    return selected, corr_matrix, sector_map


def print_report(selected: list, corr_matrix: pd.DataFrame):
    """输出最终报告。"""
    print("\n" + "=" * 60)
    print(f"最终推荐交易对（{len(selected)} 个）")
    print("=" * 60)

    pair_whitelist = []
    for i, s in enumerate(selected):
        print(
            f"  {i+1:2d}. {s['symbol']:15s} | 板块: {s['sector']:15s} | "
            f"波动率: {s['avg_volatility']*100:.1f}% | 成交额: {s['avg_volume']/1e6:.1f}M"
        )
        pair_whitelist.append(s["symbol"])

    # 配对相关性矩阵
    print(f"\n配对相关性矩阵:")
    sel_syms = [s["symbol"] for s in selected]
    header = "  " + " " * 15
    for s2 in sel_syms:
        header += f" {s2[:6]:>6s}"
    print(header)
    for s1 in sel_syms:
        row = f"  {s1:15s}"
        for s2 in sel_syms:
            if s1 in corr_matrix.columns and s2 in corr_matrix.columns:
                val = corr_matrix.loc[s1, s2]
                row += f" {val:+5.2f}"
            else:
                row += "    N/A"
        print(row)

    # config 片段
    print(f'\n可用于 config.json 的 pair_whitelist:')
    print(f'  "pair_whitelist": [')
    for p in pair_whitelist:
        print(f'    "{p}",')
    print(f'  ]')

    # 板块统计
    sectors = {}
    for s in selected:
        sectors.setdefault(s["sector"], []).append(s["symbol"])
    print(f"\n板块覆盖: {len(sectors)} 个")
    for sector, pairs in sorted(sectors.items()):
        print(f"  {sector}: {', '.join(pairs)}")
    if len(sectors) >= MIN_SECTORS:
        print("板块分散度: 合格")
    else:
        print("板块分散度不足，建议放宽相关性阈值")

    # 相关性统计
    corrs = []
    for i, s1 in enumerate(sel_syms):
        for s2 in sel_syms[i+1:]:
            if s1 in corr_matrix.columns and s2 in corr_matrix.columns:
                corrs.append(abs(corr_matrix.loc[s1, s2]))
    if corrs:
        print(f"\n相关性统计: 最高 {max(corrs):.2f}, 均值 {np.mean(corrs):.2f}, 中位 {np.median(corrs):.2f}")


def main():
    print("=" * 60)
    print("Binance USDT 现货 - 低相关交易对自动筛选（超卖反弹策略）")
    print("=" * 60)

    all_data = fetch_pairs_and_data()
    if not all_data:
        print("无法获取数据，请检查网络连接。")
        return

    # 检查上线时间
    all_data = check_listing_age(all_data)
    if not all_data:
        print("过滤后无符合条件的币对。")
        return

    selected, corr_matrix, sector_map = select_pairs(all_data)
    print_report(selected, corr_matrix)


if __name__ == "__main__":
    main()
