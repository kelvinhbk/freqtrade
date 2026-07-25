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


class ENEW_E0036(IStrategy):
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
    buy_atr_min = DecimalParameter(0.5, 3.0, default=1.0, decimals=1, space="buy", optimize=True)
    buy_atr_max = DecimalParameter(2.0, 8.0, default=5.0, decimals=1, space="buy", optimize=True)

    # Sell parameters
    sell_fastx = IntParameter(50, 100, default=62, space="sell", optimize=True)
    sell_rsi = IntParameter(50, 90, default=70, space="sell", optimize=True)
    sell_use_trend_exit = CategoricalParameter([True, False], default=True, space="sell", optimize=True)
    sell_profit_rsi = IntParameter(30, 70, default=55, space="sell", optimize=True)
    sell_profit_fastx = IntParameter(30, 80, default=50, space="sell", optimize=True)
    sell_profit_threshold = DecimalParameter(0.03, 0.15, default=0.08, decimals=3, space="sell", optimize=True)

    # Custom stoploss parameters
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 4.0, default=2.5, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.05, decimals=3, space="sell", optimize=True
    )
    csl_trail_start = DecimalParameter(
        0.05, 0.20, default=0.10, decimals=3, space="sell", optimize=True
    )
    csl_trail_offset = DecimalParameter(
        0.02, 0.10, default=0.05, decimals=3, space="sell", optimize=True
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
        dataframe["atr_sma"] = ta.SMA(dataframe["atr"], timeperiod=50)
        dataframe["atr_ratio"] = dataframe["atr"] / dataframe["atr_sma"].replace(0, 1e-9)
        
        # Dynamic EMA based on parameter
        dataframe["ema_trend"] = ta.EMA(dataframe, timeperiod=self.buy_ema_period.value)
        dataframe["price_above_ema"] = dataframe["close"] > dataframe["ema_trend"]
        dataframe["ema_slope"] = dataframe["ema_trend"] - dataframe["ema_trend"].shift(10)
        dataframe["ema_rising"] = dataframe["ema_slope"] > 0
        
        # Volume filter
        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_sma"].replace(0, 1e-9)
        
        # Pre-compute exit conditions
        dataframe["rsi_overbought"] = dataframe["rsi"] > self.sell_rsi.value
        dataframe["fastk_overbought"] = dataframe["fastk"] > self.sell_fastx.value
        dataframe["below_ema"] = dataframe["close"] < dataframe["ema_trend"]
        dataframe["was_above_ema"] = dataframe["close"].shift(1) >= dataframe["ema_trend"].shift(1)
        dataframe["ema_cross_down"] = dataframe["below_ema"] & dataframe["was_above_ema"]

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
            & (dataframe["atr_ratio"] > self.buy_atr_min.value)
            & (dataframe["atr_ratio"] < self.buy_atr_max.value)
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

        # ATR-based stop as percentage of price
        atr_pct = -(atr / current_rate) * self.csl_mid_ratio.value
        atr_pct = min(atr_pct, self.stoploss)  # Cap at initial stoploss (more negative)

        if elapsed < 30:
            # Initial phase: fixed stop
            desired_pct = self.stoploss
        elif elapsed < 240:
            # Mid phase: ATR-based dynamic stop
            desired_pct = atr_pct
        else:
            # Late phase: loosen stop but not beyond late limit
            desired_pct = max(self.csl_late.value, atr_pct)

        # Trailing stop when in profit
        if current_profit > self.csl_trail_start.value:
            # Trail with offset below highest profit
            trail_stop = -(current_profit - self.csl_trail_offset.value)
            desired_pct = max(desired_pct, trail_stop)

        # Ensure we never return positive or zero stoploss
        desired_pct = min(desired_pct, -0.001)

        # Relative to entry price
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

        # Profit-taking exit: momentum fade when in profit
        if current_profit > self.sell_profit_threshold.value:
            if (current_candle["rsi"] < self.sell_profit_rsi.value and 
                current_candle["fastk"] < self.sell_profit_fastx.value):
                return "momentum_profit_take"

        # Emergency exits
        if current_profit > 0.20:
            if current_candle["fastk"] > 90:
                return "fastk_extreme_profit"

        if current_time - timedelta(hours=12) > trade.open_date_utc:
            if current_profit >= -0.05:
                return "time_loss_sell_12_5"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_tag"] = "long_out"

        # Standard signal exit: overbought conditions
        exit_conditions = (
            dataframe["rsi_overbought"]
            & dataframe["fastk_overbought"]
        )
        
        # Optional trend-following exit: EMA cross down
        if self.sell_use_trend_exit.value:
            exit_conditions = exit_conditions | dataframe["ema_cross_down"]

        dataframe.loc[exit_conditions, "exit_tag"] = "signal_exit"
        dataframe.loc[exit_conditions, "exit_long"] = 1

        return dataframe
