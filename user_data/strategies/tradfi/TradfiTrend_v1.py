"""
TradfiTrend_v1 — 币安 TradFi 永续(美股/ETF/大宗) 1h 趋势基线

逻辑:
  入场: 收盘价突破 N 根 Donchian 上轨做多 / 跌破下轨做空, 4h EMA 趋势过滤
  过滤: 周末(UTC)不开新仓 — 实测周末成交量仅为工作日 25-39%
  出场: 反向 Donchian 通道突破(短周期), 或宽止损
设计约束: 该市场费率/波动比高, 只做持仓数根~数天的 swing, 不做 scalping
"""
from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IntParameter, IStrategy, merge_informative_pair


class TradfiTrend_v1(IStrategy):
    can_short = True
    timeframe = "1h"
    process_only_new_candles = True
    startup_candle_count = 220

    # 基线: 不用 ROI 阶梯, 让通道出场主导; 宽止损兜底
    minimal_roi = {"0": 10}
    stoploss = -0.05
    trailing_stop = False

    # --- 超参数 ---
    entry_lookback = IntParameter(12, 72, default=24, space="buy", optimize=True)
    exit_lookback = IntParameter(6, 36, default=12, space="sell", optimize=True)
    trend_ema = IntParameter(20, 100, default=50, space="buy", optimize=True)

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(p, "4h") for p in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Donchian 通道(不含当前根, 用 shift(1))
        dataframe["dc_upper"] = (
            dataframe["high"].shift(1).rolling(window=self.entry_lookback.value).max()
        )
        dataframe["dc_lower"] = (
            dataframe["low"].shift(1).rolling(window=self.entry_lookback.value).min()
        )
        dataframe["dc_exit_upper"] = (
            dataframe["high"].shift(1).rolling(window=self.exit_lookback.value).max()
        )
        dataframe["dc_exit_lower"] = (
            dataframe["low"].shift(1).rolling(window=self.exit_lookback.value).min()
        )

        # 4h 趋势过滤
        inf = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
        inf["trend_ema"] = ta.EMA(inf, timeperiod=self.trend_ema.value)
        dataframe = merge_informative_pair(
            dataframe, inf, self.timeframe, "4h", ffill=True
        )
        dataframe["trend_up"] = (dataframe["close_4h"] > dataframe["trend_ema_4h"]).astype(int)
        dataframe["trend_dn"] = (dataframe["close_4h"] < dataframe["trend_ema_4h"]).astype(int)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        weekday = dataframe["date"].dt.dayofweek < 5  # 周一~周五(UTC)

        dataframe.loc[
            weekday
            & (dataframe["close"] > dataframe["dc_upper"])
            & (dataframe["trend_up"] == 1)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "dc_break_up")

        dataframe.loc[
            weekday
            & (dataframe["close"] < dataframe["dc_lower"])
            & (dataframe["trend_dn"] == 1)
            & (dataframe["volume"] > 0),
            ["enter_short", "enter_tag"],
        ] = (1, "dc_break_dn")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] < dataframe["dc_exit_lower"]),
            ["exit_long", "exit_tag"],
        ] = (1, "dc_exit_dn")
        dataframe.loc[
            (dataframe["close"] > dataframe["dc_exit_upper"]),
            ["exit_short", "exit_tag"],
        ] = (1, "dc_exit_up")
        return dataframe
