import warnings
from datetime import datetime, timedelta

import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    CategoricalParameter,
)
from freqtrade.strategy.interface import IStrategy


warnings.simplefilter(action="ignore", category=RuntimeWarning)


class ENEW_E0023(IStrategy):
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
    buy_ema_period = IntParameter(50, 200, default=100, space="buy", optimize=True)
    buy_trend_above = CategoricalParameter([True, False], default=False, space="buy", optimize=True)
    buy_volume_ratio = DecimalParameter(0.5, 3.0, default=1.0, decimals=1, space="buy", optimize=True)

    # Sell parameters
    sell_fastx = IntParameter(50, 100, default=62, space="sell", optimize=True)
    sell_rsi = IntParameter(50, 90, default=70, space="sell", optimize=True)
    sell_use_trend_exit = CategoricalParameter([True, False], default=True, space="sell", optimize=True)

    # Custom stoploss parameters
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 3.0, default=2.3, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.062, decimals=3, space="sell", optimize=True
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
        dataframe["cti"] = pta.cti(dataframe["close"], length=20)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_fast"] = ta.RSI(dataframe, timeperiod=4)
        dataframe["rsi_slow"] = ta.RSI(dataframe, timeperiod=20)
        dataframe["24h_change_pct"] = dataframe["close"].pct_change(periods=288) * 100

        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0)
        dataframe["fastk"] = stoch_fast["fastk"]
        dataframe["cci"] = ta.CCI(dataframe, timeperiod=20)

        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        
        # Dynamic EMA based on parameter - pre-compute all possibilities or use a single value
        # Using a single representative value; hyperopt will optimize this
        dataframe["ema_trend"] = ta.EMA(dataframe, timeperiod=self.buy_ema_period.value)
        dataframe["price_above_ema"] = dataframe["close"] > dataframe["ema_trend"]
        
        # Volume filter
        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_sma"].replace(0, 1e-9)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_tag"] = ""

        conditions = (
            (dataframe["rsi_slow"] < dataframe["rsi_slow"].shift(1))
            & (dataframe["rsi_fast"] < self.buy_rsi_fast.value)
            & (dataframe["rsi"] > self.buy_rsi.value)
            & (dataframe["close"] < dataframe["sma_15"] * self.buy_sma15_ratio.value)
            & (dataframe["cti"] < self.buy_cti.value)
            & (dataframe["24h_change_pct"] > self.buy_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.buy_24h_max_pct.value)
            & (dataframe["volume_ratio"] > self.buy_volume_ratio.value)
        )
        
        # Trend filter: either require price above EMA or below EMA based on parameter
        if self.buy_trend_above.value:
            conditions = conditions & dataframe["price_above_ema"]
        else:
            conditions = conditions & ~dataframe["price_above_ema"]

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
        atr = current_candle["atr"]
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 60  # minutes

        # Ensure ATR-based stop is always negative and below initial stop
        atr_stop = -(atr / current_rate) * self.csl_mid_ratio.value
        atr_pct = min(atr_stop, self.stoploss)  # Cap at initial stoploss (more negative)

        if elapsed < 30:
            desired_pct = self.stoploss  # Use fixed initial stop, not csl_initial which equals stoploss
        elif elapsed < 240:
            desired_pct = atr_pct
        else:
            desired_pct = max(self.csl_late.value, atr_pct)  # Both negative, max = closer to 0

        # Ensure we never return positive or zero stoploss
        desired_pct = min(desired_pct, -0.001)

        # Relative to entry price, not current rate — prevents trailing
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

        # Emergency exits only - primary exits now in populate_exit_trend
        if current_profit > 0.20:
            if current_candle["fastk"] > 90:
                return "fastk_extreme_profit"

        if current_time - timedelta(hours=12) > trade.open_date_utc:
            if current_profit >= -0.05:
                return "time_loss_sell_12_5"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_tag"] = "long_out"

        exit_conditions = (
            (dataframe["rsi"] > self.sell_rsi.value)
            & (dataframe["fastk"] > self.sell_fastx.value)
        )
        
        # Optional trend-following exit: exit when price crosses below EMA
        if self.sell_use_trend_exit.value:
            exit_conditions = exit_conditions | (
                (dataframe["close"] < dataframe["ema_trend"])
                & (dataframe["close"].shift(1) >= dataframe["ema_trend"].shift(1))
            )

        dataframe.loc[exit_conditions, "exit_tag"] = "signal_exit"
        dataframe.loc[exit_conditions, "exit_long"] = 1

        return dataframe
