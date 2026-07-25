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


class EfutureLongKelvin_search(IStrategy):
    can_short = False

    minimal_roi = {"0": 0.035, "23": 0.024, "49": 0.014, "70": 0}

    # minimal_roi = {
    #     "0": 0.137,
    #     "20": 0.097,
    #     "57": 0.034,
    #     "169": 0,
    # }

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

    stoploss = -0.287
    trailing_stop = False
    use_custom_stoploss = True

    # --- Long entry parameters (mean-reversion) ---
    buy_rsi_fast = IntParameter(30, 50, default=40, space="buy", optimize=True)
    buy_rsi = IntParameter(42, 50, default=49, space="buy", optimize=True)
    buy_sma15_ratio = DecimalParameter(0.960, 1.000, default=0.980, decimals=3, space="buy", optimize=True)
    buy_cti = DecimalParameter(-0.74, 0.06, default=-0.34, decimals=2, space="buy", optimize=True)
    buy_24h_min_pct = DecimalParameter(-25.2, -13.2, default=-19.2, decimals=1, space="buy", optimize=True)
    buy_24h_max_pct = DecimalParameter(142.2, 200.0, default=182.2, decimals=1, space="buy", optimize=True)
    buy_volume_sma = DecimalParameter(0.95, 1.23, default=1.09, decimals=2, space="buy", optimize=True)
    buy_adx = IntParameter(18, 28, default=23, space="buy", optimize=True)

    # --- Trend-following entry parameters ---
    buy_tf_adx = IntParameter(31, 43, default=37, space="buy", optimize=True)
    buy_tf_rsi_min = IntParameter(39, 49, default=44, space="buy", optimize=True)
    buy_tf_rsi_max = IntParameter(55, 61, default=56, space="buy", optimize=True)

    # --- Sell parameters ---
    sell_fastx = IntParameter(72, 92, default=82, space="sell", optimize=True)
    sell_trend_filter = IntParameter(5, 9, default=6, space="sell", optimize=True)
    sell_macd_profit = DecimalParameter(0.057, 0.095, default=0.076, decimals=3, space="sell", optimize=True)
    sell_bb_middle_profit = DecimalParameter(0.021, 0.039, default=0.030, decimals=3, space="sell", optimize=True)

    # --- Time exit parameters ---
    time_exit_1_hours = IntParameter(10, 12, default=12, space="sell", optimize=True)
    time_exit_1_threshold = DecimalParameter(-0.079, -0.055, default=-0.067, decimals=3, space="sell", optimize=True)
    time_exit_2_hours = IntParameter(13, 16, default=15, space="sell", optimize=True)
    time_exit_2_threshold = DecimalParameter(-0.150, -0.125, default=-0.145, decimals=3, space="sell", optimize=True)

    # --- Custom stoploss parameters ---
    # csl_initial = -0.10
    csl_init_ratio = DecimalParameter(2.6, 3.0, default=3.0, decimals=1, space="sell", optimize=True)
    csl_mid_ratio = DecimalParameter(1.9, 2.9, default=2.4, decimals=1, space="sell", optimize=True)
    csl_late_ratio = DecimalParameter(0.2, 1.4, default=0.8, decimals=1, space="sell", optimize=True)
    # csl_late = DecimalParameter(
    #     -0.08, -0.01, default=-0.058, decimals=3, space="sell", optimize=True
    # )

    # --- Volatility filter parameters ---
    buy_atr_ratio = DecimalParameter(0.023, 0.030, default=0.028, decimals=3, space="buy", optimize=True)
    buy_atr_sma_period = IntParameter(25, 41, default=33, space="buy", optimize=True)

    # --- Strong trend filter parameters ---
    buy_trend_strength = IntParameter(24, 32, default=28, space="buy", optimize=True)

    # --- Exit Filter Parameters ---
    exit_adx_filter = IntParameter(24, 32, default=28, space="sell", optimize=True)
    exit_volatility_filter = DecimalParameter(1.0, 1.4, default=1.2, decimals=1, space="sell", optimize=True)

    # --- Trailing stop parameters ---
    trail_tier1_profit = DecimalParameter(0.063, 0.080, default=0.075, decimals=3, space="sell", optimize=True)
    trail_tier1_distance = DecimalParameter(0.033, 0.040, default=0.039, decimals=3, space="sell", optimize=True)
    trail_tier2_profit = DecimalParameter(0.128, 0.150, default=0.146, decimals=3, space="sell", optimize=True)
    trail_tier2_distance = DecimalParameter(0.007, 0.017, default=0.012, decimals=3, space="sell", optimize=True)
    trail_tier3_profit = DecimalParameter(0.206, 0.250, default=0.236, decimals=3, space="sell", optimize=True)
    trail_tier3_distance = DecimalParameter(0.012, 0.018, default=0.015, decimals=3, space="sell", optimize=True)

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
        dataframe["high_volatility"] = (
            dataframe["atr_ratio"] > dataframe["atr_ratio"].rolling(50).mean() * 1.5
        )

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
            & (dataframe["strong_uptrend"])
        )
        dataframe.loc[long_mr_conditions, "enter_tag"] += "long_mr"
        dataframe.loc[long_mr_conditions, "enter_long"] = 1

        # --- Long: Trend-following (MACD crossover + strong trend filter + momentum) ---
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
            & (dataframe["strong_uptrend"])
        )
        dataframe.loc[
            long_tf_conditions & (dataframe["enter_tag"] == ""), "enter_tag"
        ] += "long_tf"
        dataframe.loc[long_tf_conditions, "enter_long"] = 1

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
        leverage_val = trade.leverage or 1.0

        # Layer 1: Profit-based trailing (highest priority)
        # trailing_distance is price-based, multiply by leverage for position-based stoploss
        if current_profit > self.trail_tier3_profit.value:
            return -self.trail_tier3_distance.value * leverage_val
        if current_profit > self.trail_tier2_profit.value:
            return -self.trail_tier2_distance.value * leverage_val
        if current_profit > self.trail_tier1_profit.value:
            return -self.trail_tier1_distance.value * leverage_val

        # Layer 2: Time + ATR dynamic stoploss
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if len(dataframe) < 1:
            return self.stoploss

        current_candle = dataframe.iloc[-1]
        atr = current_candle["atr"]
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 60

        atr_pct_init = max(-(atr / current_rate) * self.csl_init_ratio.value, self.stoploss)
        atr_pct_mid = max(-(atr / current_rate) * self.csl_mid_ratio.value, self.stoploss)
        atr_pct_late = max(-(atr / current_rate) * self.csl_late_ratio.value, self.stoploss)

        if elapsed < 30:
            desired_pct = atr_pct_init
        elif elapsed < 90:
            desired_pct = atr_pct_mid
        else:
            desired_pct = atr_pct_late
            # desired_pct = max(self.csl_late.value, atr_pct_mid)

        # leverage_val = trade.leverage or 1.0
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
        bearish = not current_candle["ema_50_uptrend"] and not current_candle["ema_200_uptrend"]

        # Layer 1: Trend filter exit - exit when trend weakens and volatility is low
        if (
            current_candle["weak_trend"]
            and current_candle["low_volatility"]
            and current_candle["ema_50_uptrend"]
        ):
            return "trend_exit_long"

        # Layer 2: Profit exits
        if current_profit > 0:
            if current_candle["fastk"] > self.sell_fastx.value:
                return "fastk_profit_sell"
            if (
                current_profit > self.sell_macd_profit.value
                and current_candle["macd_cross_down"]
            ):
                return "macd_reversal_long"

        # Layer 3: Loss mitigation exits
        if -0.03 < current_profit < 0 and current_candle["cci"] > 80 and bearish:
            return "cci_loss_sell"

        # Layer 4: Time-based loss cuts
        if (
            current_time - timedelta(hours=self.time_exit_1_hours.value) > trade.open_date_utc
            and current_profit >= self.time_exit_1_threshold.value
        ):
            return "time_loss_1"

        if (
            current_time - timedelta(hours=self.time_exit_2_hours.value) > trade.open_date_utc
            and current_profit >= self.time_exit_2_threshold.value
        ):
            return "time_loss_2"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_short", "exit_tag"]] = (0, 0, "")
        return dataframe
