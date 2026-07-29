"""
TradfiWeekendHold_v1 — 周末持仓策略(币安 TradFi 永续)

假设: 永续周末 7x24 交易而正股闭市, 周五 RTH 收盘(UTC 20:00)买入,
周一 RTH 开盘(UTC 13:00 信号, 14:00 成交)卖出, 吃周末漂移。
探索性检验(2026-04~07, 25 周末 × 7 标的): 毛均值 +0.51%/周末, 扣费 +0.43%,
但聚类后 t=1.22, 中位数为负, 大部分可由市场 beta 解释 — 属于待证伪的弱假设。
"""
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class TradfiWeekendHold_v1(IStrategy):
    can_short = False
    timeframe = "1h"
    process_only_new_candles = True
    startup_candle_count = 2

    minimal_roi = {"0": 10}
    stoploss = -0.08  # 周末低流动性, 宽止损防插针
    trailing_stop = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["dow"] = dataframe["date"].dt.dayofweek
        dataframe["hour"] = dataframe["date"].dt.hour
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 周五 UTC 20:00 那根(RTH 收盘) -> 下一根 21:00 开盘成交
        dataframe.loc[
            (dataframe["dow"] == 4) & (dataframe["hour"] == 20) & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "fri_rth_close")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 周一 UTC 13:00 那根(RTH 开盘前) -> 下一根 14:00 开盘成交
        dataframe.loc[
            (dataframe["dow"] == 0) & (dataframe["hour"] == 13),
            ["exit_long", "exit_tag"],
        ] = (1, "mon_rth_open")
        return dataframe
