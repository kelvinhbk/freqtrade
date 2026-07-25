import warnings
from datetime import datetime, timedelta

import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
)
from freqtrade.strategy.interface import IStrategy


warnings.simplefilter(action="ignore", category=RuntimeWarning)


class ENEW_E0005(IStrategy):
    minimal_roi = {
        "0": 0.15,
        "30": 0.08,
        "60": 0.05,
        "120": 0.03,
    }

    timeframe = "5m"
    process_only_new_candles = True
    startup_candle_count = 310

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
        "stoploss_on_exchange_interval": 60,
        "stoploss_on_exchange_market_ratio": 0.99,
    }

    stoploss = -0.10

    trailing_stop = False

    use_custom_stoploss = True

    # Buy parameters
    buy_rsi_fast = IntParameter(20, 70, default=68, space="buy", optimize=True)
    buy_rsi = IntParameter(15, 50, default=15, space="buy", optimize=True)
    buy_sma15_ratio = DecimalParameter(
        0.90, 1.0, default=0.954, decimals=3, space="buy", optimize=True
    )
    buy_cti = DecimalParameter(-1, 1, default=-0.75, decimals=2, space="buy", optimize=True)
    buy_24h_min_pct = DecimalParameter(
        -30.0, 0.0, default=-26.3, decimals=1, space="buy", optimize=True
    )
    buy_24h_max_pct = DecimalParameter(
        0.0, 200.0, default=50.0, decimals=1, space="buy", optimize=True
    )
    
    # New regime filter parameters
    buy_adx_max = IntParameter(10, 50, default=35, space="buy", optimize=True)
    buy_volume_ratio = DecimalParameter(0.5, 3.0, default=1.0, decimals=1, space="buy", optimize=True)

    # Sell parameters
    sell_fastx = IntParameter(50, 100, default=62, space="sell", optimize=True)

    # Custom stoploss parameters
    csl_atr_mult = DecimalParameter(
        1.5, 4.0, default=2.5, decimals=1, space="sell", optimize=True
    )
    csl_profit_atr_mult = DecimalParameter(
        1.0, 3.0, default=1.5, decimals=1, space="sell", optimize=True
    )

    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 96,
            }
        ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["sma_15"] = ta.SMA(dataframe, timeperiod=15)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["cti"] = pta.cti(dataframe["close"], length=20)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_fast"] = ta.RSI(dataframe, timeperiod=4)
        dataframe["rsi_slow"] = ta.RSI(dataframe, timeperiod=20)
        dataframe["24h_change_pct"] = dataframe["close"].pct_change(periods=288) * 100

        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0)
        dataframe["fastk"] = stoch_fast["fastk"]
        dataframe["cci"] = ta.CCI(dataframe, timeperiod=20)

        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_14_pct"] = dataframe["atr"] / dataframe["close"]
        
        # Trend regime indicators
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["ema_200_dist"] = (dataframe["close"] - dataframe["ema_200"]) / dataframe["ema_200"]
        
        # Volume confirmation
        dataframe["volume_sma_20"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_sma_20"]
        
        # Pre-compute trend regime for use in custom functions
        # Mean reversion works best when: not in strong trend, price near or below EMA200
        dataframe["weak_trend"] = dataframe["adx"] < dataframe["adx"].shift(1)
        dataframe["price_below_ema200"] = dataframe["close"] < dataframe["ema_200"]

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_tag"] = ""

        # Core mean reversion conditions (preserved from baseline)
        core_conditions = (
            (dataframe["rsi_fast"] < self.buy_rsi_fast.value)
            & (dataframe["rsi"] > self.buy_rsi.value)
            & (dataframe["close"] < dataframe["sma_15"] * self.buy_sma15_ratio.value)
            & (dataframe["cti"] < self.buy_cti.value)
            & (dataframe["24h_change_pct"] > self.buy_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.buy_24h_max_pct.value)
        )
        
        # Regime filters: avoid strong trends, ensure some volume
        regime_conditions = (
            (dataframe["adx"] < self.buy_adx_max.value)
            & (dataframe["volume_ratio"] > self.buy_volume_ratio.value)
        )

        conditions = core_conditions & regime_conditions

        dataframe.loc[conditions, "enter_tag"] += "buy_1"
        dataframe.loc[conditions, "enter_long"] = 1

        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if len(dataframe) < 1:
            return self.stoploss

        current_candle = dataframe.iloc[-1]
        atr_pct = current_candle["atr_14_pct"]
        
        # Dynamic stoploss based on ATR and profit level
        # Wider stop when no profit, tighter as profit builds
        if current_profit < 0:
            # In loss: use wider ATR-based stop to avoid noise
            desired_pct = max(-(atr_pct * self.csl_atr_mult.value), self.stoploss)
        else:
            # In profit: tighten to lock in gains
            trailing_atr = atr_pct * self.csl_profit_atr_mult.value
            # Stop trails below entry by atr-based amount, or breakeven if profit sufficient
            desired_pct = max(trailing_atr, -0.02)  # Never tighter than -2% once in profit
        
        # Convert to return format relative to current rate
        desired_stop_price = trade.open_rate * (1 + desired_pct)
        return (desired_stop_price / current_rate) - 1

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if len(dataframe) < 1:
            return None
        current_candle = dataframe.iloc[-1]

        # Profit exit: momentum exhaustion
        if current_profit > 0:
            if current_candle["fastk"] > self.sell_fastx.value:
                return "fastk_profit_sell"

        # Small loss exit: structure breakdown (price below EMA200 in weak position)
        if -0.03 < current_profit < 0:
            if current_candle["cci"] > 80:
                return "cci_loss_sell"
            # New: exit if trend turned strongly against us
            if current_candle["adx"] > 40 and current_candle["price_below_ema200"]:
                return "strong_trend_against"

        # Time-based exits for stale trades
        if current_time - timedelta(hours=7) > trade.open_date_utc:
            if current_profit >= -0.05:
                return "time_loss_sell_7_5"

        if current_time - timedelta(hours=10) > trade.open_date_utc:
            if current_profit >= -0.10:
                return "time_loss_sell_10_10"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_tag"]] = (0, "long_out")
        return dataframe
