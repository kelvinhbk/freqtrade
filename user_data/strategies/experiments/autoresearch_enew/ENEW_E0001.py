import warnings
from datetime import datetime, timedelta

import numpy as np
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


class ENEW_E0001(IStrategy):
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

    # Buy parameters - PRESERVED EXACTLY from baseline
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

    # Sell parameters
    sell_fastx = IntParameter(50, 100, default=62, space="sell", optimize=True)

    # Custom stoploss parameters - PRESERVED
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 3.0, default=2.3, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.062, decimals=3, space="sell", optimize=True
    )

    # NEW: Adaptive exit parameters (replacing hardcoded values)
    exit_time_1_hours = IntParameter(2, 12, default=7, space="sell", optimize=True)
    exit_time_1_profit = DecimalParameter(-0.10, 0.05, default=-0.05, decimals=3, space="sell", optimize=True)
    exit_time_2_hours = IntParameter(4, 16, default=10, space="sell", optimize=True)
    exit_time_2_profit = DecimalParameter(-0.15, 0.0, default=-0.10, decimals=3, space="sell", optimize=True)
    
    # Volatility regime threshold for exit adjustment
    exit_atr_percentile = IntParameter(50, 90, default=70, space="sell", optimize=True)
    exit_cci_tighten = IntParameter(50, 120, default=80, space="sell", optimize=True)

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
        
        # Pre-compute volatility regime indicator for adaptive exits
        # rolling percentile of ATR over 7 days (2016 5m candles)
        dataframe["atr_rank"] = dataframe["atr"].rolling(window=2016, min_periods=100).apply(
            lambda x: np.searchsorted(np.sort(x), x.iloc[-1]) / len(x) * 100 if len(x) > 0 else 50, 
            raw=False
        )
        # Fill NaN with neutral value
        dataframe["atr_rank"] = dataframe["atr_rank"].fillna(50)
        # Boolean: is current ATR in high percentile (volatile regime)?
        # Pre-computed to avoid series comparison in custom_exit
        dataframe["high_vol_regime"] = dataframe["atr_rank"] > self.exit_atr_percentile.value

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
        )

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

        atr_pct = max(-(atr / current_rate) * self.csl_mid_ratio.value, self.stoploss)

        if elapsed < 30:
            desired_pct = self.csl_initial
        elif elapsed < 240:
            desired_pct = atr_pct
        else:
            desired_pct = max(self.csl_late.value, atr_pct)

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

        # Use pre-computed volatility regime (scalar boolean)
        high_vol = current_candle["high_vol_regime"]
        
        # Adaptive CCI threshold: tighter in high volatility
        cci_threshold = self.exit_cci_tighten.value if high_vol else 80

        if current_profit > 0:
            if current_candle["fastk"] > self.sell_fastx.value:
                return "fastk_profit_sell"

        # Adaptive small loss exit based on volatility regime
        if -0.03 < current_profit < 0:
            if current_candle["cci"] > cci_threshold:
                return "cci_loss_sell"

        # Time-based exits with tunable parameters instead of hardcoded values
        time_1_min = self.exit_time_1_hours.value * 60
        time_2_min = self.exit_time_2_hours.value * 60
        
        if (current_time - trade.open_date_utc).total_seconds() / 60 > time_1_min:
            if current_profit >= self.exit_time_1_profit.value:
                return "time_loss_sell_1"

        if (current_time - trade.open_date_utc).total_seconds() / 60 > time_2_min:
            if current_profit >= self.exit_time_2_profit.value:
                return "time_loss_sell_2"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_tag"]] = (0, "long_out")
        return dataframe
