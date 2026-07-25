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


class AutoResearch_iter0021(IStrategy):
    can_short = True

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

    # --- Long entry parameters ---
    buy_rsi_fast = IntParameter(20, 70, default=21, space="buy", optimize=True)
    buy_rsi = IntParameter(15, 50, default=18, space="buy", optimize=True)
    buy_sma15_ratio = DecimalParameter(
        0.90, 1.0, default=0.977, decimals=3, space="buy", optimize=True
    )
    buy_cti = DecimalParameter(-1, 1, default=-0.51, decimals=2, space="buy", optimize=True)
    buy_24h_min_pct = DecimalParameter(
        -30.0, 0.0, default=-13.4, decimals=1, space="buy", optimize=True
    )
    buy_24h_max_pct = DecimalParameter(
        0.0, 200.0, default=58.4, decimals=1, space="buy", optimize=True
    )
    
    # --- Trend filter parameters ---
    buy_adx_threshold = IntParameter(15, 35, default=25, space="buy", optimize=True)
    buy_volatility_min = DecimalParameter(
        0.5, 3.0, default=1.5, decimals=2, space="buy", optimize=True
    )
    buy_volatility_max = DecimalParameter(
        2.0, 8.0, default=4.0, decimals=2, space="buy", optimize=True
    )

    # --- Short entry parameters (mirror of long) ---
    short_rsi_fast = IntParameter(30, 80, default=80, space="buy", optimize=True)
    short_rsi = IntParameter(50, 85, default=85, space="buy", optimize=True)
    short_sma15_ratio = DecimalParameter(
        1.0, 1.10, default=1.023, decimals=3, space="buy", optimize=True
    )
    short_cti = DecimalParameter(
        -1, 1, default=0.51, decimals=2, space="buy", optimize=True
    )
    short_24h_min_pct = DecimalParameter(
        -200.0, 0.0, default=-58.4, decimals=1, space="buy", optimize=True
    )
    short_24h_max_pct = DecimalParameter(
        0.0, 30.0, default=13.4, decimals=1, space="buy", optimize=True
    )

    # --- Sell parameters ---
    sell_fastx = IntParameter(50, 100, default=92, space="sell", optimize=True)
    sell_trend_filter = IntParameter(5, 20, default=10, space="sell", optimize=True)

    # --- Custom stoploss parameters ---
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 3.0, default=1.1, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.044, decimals=3, space="sell", optimize=True
    )

    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 96,
            }
        ]

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 3.0

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
        
        # ADX for trend strength
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["di_plus"] = ta.PLUS_DM(dataframe, timeperiod=14)
        dataframe["di_minus"] = ta.MINUS_DM(dataframe, timeperiod=14)

        # Add trend indicators
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["ema_50_uptrend"] = dataframe["ema_50"] > dataframe["ema_50"].shift(20)
        dataframe["ema_200_uptrend"] = dataframe["ema_200"] > dataframe["ema_200"].shift(20)
        
        # Add volatility filter
        dataframe["volatility"] = dataframe["atr"] / dataframe["close"] * 100
        
        # Simplified trend filter - just ADX strength
        dataframe["trend_strength"] = dataframe["adx"]
        
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_tag"] = ""

        # --- Simplified trend filter ---
        adx_threshold = self.buy_adx_threshold.value
        
        # --- Long: oversold bounce with trend and volatility filters ---
        long_conditions = (
            (dataframe["rsi_slow"] < dataframe["rsi_slow"].shift(1))
            & (dataframe["rsi_fast"] < self.buy_rsi_fast.value)
            & (dataframe["rsi"] > self.buy_rsi.value)
            & (dataframe["close"] < dataframe["sma_15"] * self.buy_sma15_ratio.value)
            & (dataframe["cti"] < self.buy_cti.value)
            & (dataframe["24h_change_pct"] > self.buy_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.buy_24h_max_pct.value)
            & (dataframe["trend_strength"] > adx_threshold)  # Only require strong trend
            & (dataframe["volatility"] > self.buy_volatility_min.value)
            & (dataframe["volatility"] < self.buy_volatility_max.value)
        )
        dataframe.loc[long_conditions, "enter_tag"] += "long_1"
        dataframe.loc[long_conditions, "enter_long"] = 1

        # --- Short: overbought reversal with trend and volatility filters ---
        short_conditions = (
            (dataframe["rsi_slow"] > dataframe["rsi_slow"].shift(1))
            & (dataframe["rsi_fast"] > self.short_rsi_fast.value)
            & (dataframe["rsi"] < self.short_rsi.value)
            & (dataframe["close"] > dataframe["sma_15"] * self.short_sma15_ratio.value)
            & (dataframe["cti"] > self.short_cti.value)
            & (dataframe["24h_change_pct"] > self.short_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.short_24h_max_pct.value)
            & (dataframe["trend_strength"] > adx_threshold)  # Only require strong trend
            & (dataframe["volatility"] > self.buy_volatility_min.value)
            & (dataframe["volatility"] < self.buy_volatility_max.value)
        )
        dataframe.loc[short_conditions & (dataframe["enter_tag"] == ""), "enter_tag"] += "short_1"
        dataframe.loc[short_conditions, "enter_short"] = 1

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
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 60

        atr_pct = max(-(atr / current_rate) * self.csl_mid_ratio.value, self.stoploss)

        if elapsed < 30:
            desired_pct = self.csl_initial
        elif elapsed < 240:
            desired_pct = atr_pct
        else:
            desired_pct = max(self.csl_late.value, atr_pct)

        # Fixed stop relative to entry price, accounting for leverage and direction
        leverage_val = trade.leverage or 1.0
        if trade.is_short:
            # Short: stop above entry (loss = price rises)
            desired_stop_price = trade.open_rate * (1 - desired_pct)
            return leverage_val * (1 - desired_stop_price / current_rate)
        else:
            # Long: stop below entry (loss = price drops)
            desired_stop_price = trade.open_rate * (1 + desired_pct)
            return leverage_val * (desired_stop_price / current_rate - 1)

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
        
        # Get trend filter period
        trend_period = self.sell_trend_filter.value

        # Check trend direction using pre-computed columns
        if trade.is_short:
            # For short positions, uptrend is bearish for the position
            bearish = current_candle["ema_50_uptrend"]
        else:
            # For long positions, downtrend is bearish for the position
            bearish = not current_candle["ema_50_uptrend"]

        if current_profit > 0:
            if trade.is_short:
                # Short profit exit: fastk drops below (100 - sell_fastx)
                if current_candle["fastk"] < (100 - self.sell_fastx.value):
                    return "fastk_profit_short"
            else:
                # Long profit exit: fastk rises above sell_fastx
                if current_candle["fastk"] > self.sell_fastx.value:
                    return "fastk_profit_sell"

        if -0.03 < current_profit < 0:
            if trade.is_short:
                if current_candle["cci"] < -80 and bearish:
                    return "cci_loss_short"
            else:
                if current_candle["cci"] > 80 and bearish:
                    return "cci_loss_sell"

        if current_time - timedelta(hours=7) > trade.open_date_utc:
            if current_profit >= -0.05:
                return "time_loss_7_5"

        if current_time - timedelta(hours=10) > trade.open_date_utc:
            if current_profit >= -0.10:
                return "time_loss_10_10"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_short", "exit_tag"]] = (0, 0, "")
        return dataframe
