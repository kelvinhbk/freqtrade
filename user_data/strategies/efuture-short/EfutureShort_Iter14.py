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


class EfutureShort_Iter14(IStrategy):
    """
    EfutureShort with Iteration 14 optimized parameters.
    Recovered from autoresearch evolution (gen=1, score=1.1811).
    IS: 46 trades, +49.07% profit, Sharpe 0.786, PF 3.159, DD 4.4%
    OOS: 11 trades, +11.88% profit, Sharpe 1.174, PF 3.566, DD 1.8%
    """

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

    # --- Short entry parameters (mean-reversion) ---
    short_rsi_fast = IntParameter(30, 80, default=48, space="buy", optimize=True)
    short_rsi = IntParameter(50, 85, default=57, space="buy", optimize=True)
    short_sma15_ratio = DecimalParameter(
        1.0, 1.10, default=1.023, decimals=3, space="buy", optimize=True
    )
    short_cti = DecimalParameter(
        -1, 1, default=-0.74, decimals=2, space="buy", optimize=True
    )
    short_24h_min_pct = DecimalParameter(
        -200.0, 0.0, default=-15.2, decimals=1, space="buy", optimize=True
    )
    short_24h_max_pct = DecimalParameter(
        0.0, 30.0, default=5.6, decimals=1, space="buy", optimize=True
    )
    short_volume_sma = DecimalParameter(
        0.8, 1.5, default=1.47, decimals=2, space="buy", optimize=True
    )
    short_adx = IntParameter(15, 40, default=17, space="buy", optimize=True)

    # --- Short trend-following entry parameters ---
    short_tf_adx = IntParameter(15, 45, default=16, space="buy", optimize=True)
    short_tf_rsi_min = IntParameter(20, 45, default=43, space="buy", optimize=True)
    short_tf_rsi_max = IntParameter(45, 70, default=66, space="buy", optimize=True)

    # --- Sell parameters ---
    sell_fastx = IntParameter(50, 100, default=56, space="sell", optimize=True)
    sell_trend_filter = IntParameter(5, 20, default=5, space="sell", optimize=True)
    sell_macd_profit = DecimalParameter(
        0.005, 0.10, default=0.064, decimals=3, space="sell", optimize=True
    )
    sell_bb_middle_profit = DecimalParameter(
        0.005, 0.05, default=0.05, decimals=3, space="sell", optimize=True
    )

    # --- Time exit parameters ---
    time_exit_1_hours = IntParameter(4, 12, default=5, space="sell", optimize=True)
    time_exit_1_threshold = DecimalParameter(
        -0.08, -0.02, default=-0.038, decimals=3, space="sell", optimize=True
    )
    time_exit_2_hours = IntParameter(8, 16, default=10, space="sell", optimize=True)
    time_exit_2_threshold = DecimalParameter(
        -0.15, -0.05, default=-0.086, decimals=3, space="sell", optimize=True
    )

    # --- Custom stoploss parameters ---
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 3.0, default=2.5, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.048, decimals=3, space="sell", optimize=True
    )

    # --- Strong trend filter parameters ---
    short_trend_strength = IntParameter(20, 40, default=39, space="buy", optimize=True)

    # --- Exit filter parameters ---
    exit_adx_filter = IntParameter(15, 35, default=34, space="sell", optimize=True)
    exit_volatility_filter = DecimalParameter(
        0.8, 2.0, default=1.7, decimals=1, space="sell", optimize=True
    )

    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 48,
            }
        ]

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 3.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["sma_15"] = ta.SMA(dataframe, timeperiod=15)
        dataframe["volume_sma"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["cti"] = pta.cti(dataframe["close"], length=20)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_fast"] = ta.RSI(dataframe, timeperiod=4)
        dataframe["rsi_slow"] = ta.RSI(dataframe, timeperiod=20)
        dataframe["24h_change_pct"] = dataframe["close"].pct_change(periods=288) * 100

        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0)
        dataframe["fastk"] = stoch_fast["fastk"]
        dataframe["cci"] = ta.CCI(dataframe, timeperiod=20)

        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        # Volatility filter (hardcoded period 30)
        dataframe["atr_sma"] = ta.SMA(dataframe["atr"], timeperiod=30)
        dataframe["atr_ratio"] = dataframe["atr"] / dataframe["close"]
        dataframe["high_volatility"] = (
            dataframe["atr_ratio"] > dataframe["atr_ratio"].rolling(50).mean() * 1.5
        )

        # Trend EMAs for exit filter
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["ema_50_uptrend"] = (
            dataframe["ema_50"] > dataframe["ema_50"].shift(self.sell_trend_filter.value)
        )
        dataframe["ema_200_uptrend"] = (
            dataframe["ema_200"] > dataframe["ema_200"].shift(self.sell_trend_filter.value)
        )

        # MACD for trend-following entries and reversal exits
        macd_result = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd_result["macd"]
        dataframe["macd_signal"] = macd_result["macdsignal"]
        dataframe["macd_hist"] = macd_result["macdhist"]

        dataframe["macd_cross_down"] = (
            (dataframe["macd"] < dataframe["macd_signal"])
            & (dataframe["macd"].shift(1) >= dataframe["macd_signal"].shift(1))
        )
        dataframe["macd_cross_up"] = (
            (dataframe["macd"] > dataframe["macd_signal"])
            & (dataframe["macd"].shift(1) <= dataframe["macd_signal"].shift(1))
        )

        # Strong downtrend filter
        dataframe["strong_downtrend"] = dataframe["adx"] > self.short_trend_strength.value

        # Exit filters
        dataframe["weak_trend"] = dataframe["adx"] < self.exit_adx_filter.value
        dataframe["low_volatility"] = (
            dataframe["atr_ratio"]
            < dataframe["atr_ratio"].rolling(50).mean() * self.exit_volatility_filter.value
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_tag"] = ""

        # Volume filter for all entries
        volume_filter = dataframe["volume"] > dataframe["volume_sma"] * 1.2

        # Volatility filter - avoid high volatility periods
        volatility_filter = ~dataframe["high_volatility"]

        # --- Short: Mean-reversion (overbought reversal with volume + ADX + volatility filter) ---
        short_mr_conditions = (
            volume_filter
            & volatility_filter
            & (dataframe["rsi"] > self.short_rsi.value)
            & (dataframe["rsi_fast"] > self.short_rsi_fast.value)
            & (dataframe["close"] > dataframe["sma_15"] * self.short_sma15_ratio.value)
            & (dataframe["cti"] > self.short_cti.value)
            & (dataframe["24h_change_pct"] > self.short_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.short_24h_max_pct.value)
            & (dataframe["adx"] > self.short_adx.value)
            & (dataframe["strong_downtrend"])
        )
        dataframe.loc[
            short_mr_conditions & (dataframe["enter_tag"] == ""), "enter_tag"
        ] += "short_mr"
        dataframe.loc[short_mr_conditions, "enter_short"] = 1

        # --- Short: Trend-following (MACD crossunder + strong trend filter + momentum) ---
        short_tf_conditions = (
            volume_filter
            & volatility_filter
            & (dataframe["macd_cross_down"])
            & (dataframe["ema_50"] < dataframe["ema_200"])
            & (~dataframe["ema_50_uptrend"])
            & (~dataframe["ema_200_uptrend"])
            & (dataframe["adx"] > self.short_tf_adx.value)
            & (dataframe["rsi"] > self.short_tf_rsi_min.value)
            & (dataframe["rsi"] < self.short_tf_rsi_max.value)
            & (dataframe["strong_downtrend"])
        )
        dataframe.loc[
            short_tf_conditions & (dataframe["enter_tag"] == ""), "enter_tag"
        ] += "short_tf"
        dataframe.loc[short_tf_conditions, "enter_short"] = 1

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

        leverage_val = trade.leverage or 1.0
        desired_stop_price = trade.open_rate * (1 - desired_pct)
        return leverage_val * (1 - desired_stop_price / current_rate)

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

        # For short trades: bearish means EMAs pointing up (price may reverse against short)
        bearish = current_candle["ema_50_uptrend"] and current_candle["ema_200_uptrend"]

        # Layer 1: Trend filter exit - exit when trend weakens and volatility is low
        if current_candle["weak_trend"] and current_candle["low_volatility"]:
            if not current_candle["ema_50_uptrend"]:
                return "trend_exit_short"

        # Layer 2: Profit exits
        if current_profit > 0:
            # Fastk-based profit exit
            if current_candle["fastk"] < (100 - self.sell_fastx.value):
                return "fastk_profit_short"

            # MACD reversal exit: lock in profit when momentum reverses
            if current_profit > self.sell_macd_profit.value:
                if current_candle["macd_cross_up"]:
                    return "macd_reversal_short"

        # Layer 3: Loss mitigation exits
        if -0.03 < current_profit < 0:
            if current_candle["cci"] < -80 and bearish:
                return "cci_loss_short"

        # Layer 4: Time-based loss cuts
        if current_time - timedelta(hours=self.time_exit_1_hours.value) > trade.open_date_utc:
            if current_profit >= self.time_exit_1_threshold.value:
                return "time_loss_1"

        if current_time - timedelta(hours=self.time_exit_2_hours.value) > trade.open_date_utc:
            if current_profit >= self.time_exit_2_threshold.value:
                return "time_loss_2"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_short", "exit_tag"]] = (0, 0, "")
        return dataframe
