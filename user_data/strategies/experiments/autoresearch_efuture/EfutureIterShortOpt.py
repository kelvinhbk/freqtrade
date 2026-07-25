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


class EfutureIterShortOpt(IStrategy):
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

    # --- Long entry parameters (mean-reversion) ---
    buy_rsi_fast = IntParameter(20, 70, default=21, space="buy", optimize=False)
    buy_rsi = IntParameter(15, 50, default=18, space="buy", optimize=False)
    buy_sma15_ratio = DecimalParameter(
        0.90, 1.0, default=0.977, decimals=3, space="buy", optimize=False
    )
    buy_cti = DecimalParameter(-1, 1, default=-0.51, decimals=2, space="buy", optimize=False)
    buy_24h_min_pct = DecimalParameter(
        -30.0, 0.0, default=-13.4, decimals=1, space="buy", optimize=False
    )
    buy_24h_max_pct = DecimalParameter(
        0.0, 200.0, default=58.4, decimals=1, space="buy", optimize=False
    )
    buy_volume_sma = DecimalParameter(
        0.8, 1.5, default=1.1, decimals=2, space="buy", optimize=False
    )
    buy_adx = IntParameter(15, 40, default=25, space="buy", optimize=False)

    # --- Short entry parameters (mean-reversion) ---
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
    short_volume_sma = DecimalParameter(
        0.8, 1.5, default=1.1, decimals=2, space="buy", optimize=True
    )
    short_adx = IntParameter(15, 40, default=25, space="buy", optimize=True)

    # --- Trend-following entry parameters ---
    buy_tf_adx = IntParameter(15, 45, default=25, space="buy", optimize=False)
    buy_tf_rsi_min = IntParameter(30, 55, default=40, space="buy", optimize=False)
    buy_tf_rsi_max = IntParameter(55, 80, default=70, space="buy", optimize=False)

    short_tf_adx = IntParameter(15, 45, default=25, space="buy", optimize=True)
    short_tf_rsi_min = IntParameter(20, 45, default=30, space="buy", optimize=True)
    short_tf_rsi_max = IntParameter(45, 70, default=60, space="buy", optimize=True)

    # --- Sell parameters ---
    sell_fastx = IntParameter(50, 100, default=92, space="sell", optimize=True)
    sell_trend_filter = IntParameter(5, 20, default=10, space="sell", optimize=True)
    sell_macd_profit = DecimalParameter(
        0.005, 0.10, default=0.02, decimals=3, space="sell", optimize=True
    )
    sell_bb_middle_profit = DecimalParameter(
        0.005, 0.05, default=0.015, decimals=3, space="sell", optimize=True
    )

    # --- Time exit parameters (NEW - tunable) ---
    time_exit_1_hours = IntParameter(4, 12, default=7, space="sell", optimize=True)
    time_exit_1_threshold = DecimalParameter(
        -0.08, -0.02, default=-0.05, decimals=3, space="sell", optimize=True
    )
    time_exit_2_hours = IntParameter(8, 16, default=10, space="sell", optimize=True)
    time_exit_2_threshold = DecimalParameter(
        -0.15, -0.05, default=-0.10, decimals=3, space="sell", optimize=True
    )

    # --- Custom stoploss parameters ---
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 3.0, default=1.1, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.044, decimals=3, space="sell", optimize=True
    )

    # --- Volatility filter parameters ---
    buy_atr_ratio = DecimalParameter(
        0.005, 0.03, default=0.015, decimals=3, space="buy", optimize=False
    )
    buy_atr_sma_period = IntParameter(10, 50, default=30, space="buy", optimize=False)

    # --- Strong trend filter parameters ---
    buy_trend_strength = IntParameter(20, 40, default=30, space="buy", optimize=False)
    short_trend_strength = IntParameter(20, 40, default=30, space="buy", optimize=True)

    # --- New Exit Filter Parameters ---
    exit_adx_filter = IntParameter(15, 35, default=25, space="sell", optimize=True)
    exit_volatility_filter = DecimalParameter(
        0.8, 2.0, default=1.5, decimals=1, space="sell", optimize=True
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
        
        # ATR-based volatility filter
        dataframe["atr_sma"] = ta.SMA(dataframe["atr"], timeperiod=self.buy_atr_sma_period.value)
        dataframe["atr_ratio"] = dataframe["atr"] / dataframe["close"]
        dataframe["high_volatility"] = dataframe["atr_ratio"] > dataframe["atr_ratio"].rolling(50).mean() * 1.5

        # Trend EMAs for trend filter and exit filter
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

        # Pre-compute MACD crossover signals
        dataframe["macd_cross_up"] = (
            (dataframe["macd"] > dataframe["macd_signal"])
            & (dataframe["macd"].shift(1) <= dataframe["macd_signal"].shift(1))
        )
        dataframe["macd_cross_down"] = (
            (dataframe["macd"] < dataframe["macd_signal"])
            & (dataframe["macd"].shift(1) >= dataframe["macd_signal"].shift(1))
        )

        # Strong trend filter using ADX
        dataframe["strong_uptrend"] = dataframe["adx"] > self.buy_trend_strength.value
        dataframe["strong_downtrend"] = dataframe["adx"] > self.short_trend_strength.value
        
        # New exit filters
        dataframe["weak_trend"] = dataframe["adx"] < self.exit_adx_filter.value
        dataframe["low_volatility"] = dataframe["atr_ratio"] < dataframe["atr_ratio"].rolling(50).mean() * self.exit_volatility_filter.value

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_tag"] = ""
        
        # Volume filter for all entries
        volume_filter = dataframe["volume"] > dataframe["volume_sma"] * 1.2
        
        # Volatility filter - avoid high volatility periods
        volatility_filter = ~dataframe["high_volatility"]

        # --- Long: Mean-reversion (oversold bounce with volume + ADX + volatility filter) ---
        long_mr_conditions = (
            volume_filter
            & volatility_filter
            & (dataframe["rsi"] < self.buy_rsi.value)
            & (dataframe["rsi_fast"] < self.buy_rsi_fast.value)
            & (dataframe["close"] < dataframe["sma_15"] * self.buy_sma15_ratio.value)
            & (dataframe["cti"] < self.buy_cti.value)
            & (dataframe["24h_change_pct"] > self.buy_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.buy_24h_max_pct.value)
            & (dataframe["adx"] > self.buy_adx.value)
            & (dataframe["strong_uptrend"])  # Strong trend filter
        )
        dataframe.loc[long_mr_conditions, "enter_tag"] += "long_mr"
        dataframe.loc[long_mr_conditions, "enter_long"] = 1

        # --- Long: Trend-following (MACD crossover + strong trend filter + momentum + volatility filter) ---
        long_tf_conditions = (
            volume_filter
            & volatility_filter
            & (dataframe["macd_cross_up"])
            & (dataframe["ema_50"] > dataframe["ema_200"])
            & (dataframe["ema_50_uptrend"])
            & (dataframe["ema_200_uptrend"])
            & (dataframe["adx"] > self.buy_tf_adx.value)
            & (dataframe["rsi"] > self.buy_tf_rsi_min.value)
            & (dataframe["rsi"] < self.buy_tf_rsi_max.value)
            & (dataframe["strong_uptrend"])  # Strong trend filter
        )
        dataframe.loc[
            long_tf_conditions & (dataframe["enter_tag"] == ""), "enter_tag"
        ] += "long_tf"
        dataframe.loc[long_tf_conditions, "enter_long"] = 1

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
            & (dataframe["strong_downtrend"])  # Strong trend filter
        )
        dataframe.loc[
            short_mr_conditions & (dataframe["enter_tag"] == ""), "enter_tag"
        ] += "short_mr"
        dataframe.loc[short_mr_conditions, "enter_short"] = 1

        # --- Short: Trend-following (MACD crossunder + strong trend filter + momentum + volatility filter) ---
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
            & (dataframe["strong_downtrend"])  # Strong trend filter
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
        if trade.is_short:
            desired_stop_price = trade.open_rate * (1 - desired_pct)
            return leverage_val * (1 - desired_stop_price / current_rate)
        else:
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

        # Check trend direction using pre-computed columns
        if trade.is_short:
            bearish = current_candle["ema_50_uptrend"] and current_candle["ema_200_uptrend"]
        else:
            bearish = not current_candle["ema_50_uptrend"] and not current_candle["ema_200_uptrend"]

        # --- New: Enhanced Exit Logic Framework ---
        
        # Layer 1: Trend filter exit - exit when trend weakens and volatility is low
        if current_candle["weak_trend"] and current_candle["low_volatility"]:
            if trade.is_short and not current_candle["ema_50_uptrend"]:
                return "trend_exit_short"
            if not trade.is_short and current_candle["ema_50_uptrend"]:
                return "trend_exit_long"
        
        # Layer 2: Profit exits (preserved from original)
        if current_profit > 0:
            # Fastk-based profit exit
            if trade.is_short:
                if current_candle["fastk"] < (100 - self.sell_fastx.value):
                    return "fastk_profit_short"
            else:
                if current_candle["fastk"] > self.sell_fastx.value:
                    return "fastk_profit_sell"

            # MACD reversal exit: lock in profit when momentum reverses
            if current_profit > self.sell_macd_profit.value:
                if trade.is_short and current_candle["macd_cross_up"]:
                    return "macd_reversal_short"
                if not trade.is_short and current_candle["macd_cross_down"]:
                    return "macd_reversal_long"

        # Layer 3: Loss mitigation exits (preserved from original)
        if -0.03 < current_profit < 0:
            if trade.is_short:
                if current_candle["cci"] < -80 and bearish:
                    return "cci_loss_short"
            else:
                if current_candle["cci"] > 80 and bearish:
                    return "cci_loss_sell"

        # Layer 4: Time-based loss cuts (preserved from original)
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
