import warnings
from datetime import datetime, timedelta

import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
)
from freqtrade.strategy.interface import IStrategy


warnings.simplefilter(action="ignore", category=RuntimeWarning)


class ENEW_v2(IStrategy):
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

    # Buy parameters (loaded from ENEW.json optimized values)
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

    # Trend & volume filters
    use_trend_filter = BooleanParameter(default=False, space="buy", optimize=True)
    use_volume_filter = BooleanParameter(default=False, space="buy", optimize=True)

    # Sell parameters (loaded from ENEW.json optimized values)
    sell_fastx = IntParameter(50, 100, default=92, space="sell", optimize=True)

    # Custom stoploss parameters
    csl_initial = -0.10
    csl_mid_ratio = DecimalParameter(
        1.0, 3.0, default=1.1, decimals=1, space="sell", optimize=True
    )
    csl_late = DecimalParameter(
        -0.08, -0.01, default=-0.044, decimals=3, space="sell", optimize=True
    )

    # Custom exit parameters
    exit_profit_fastk = DecimalParameter(
        0.0, 0.05, default=0.0, decimals=3, space="sell", optimize=True
    )
    exit_loss_cci_threshold = IntParameter(50, 100, default=80, space="sell", optimize=True)
    exit_loss_cci_profit_min = DecimalParameter(
        -0.05, 0.0, default=-0.03, decimals=3, space="sell", optimize=True
    )
    exit_loss_cci_profit_max = DecimalParameter(
        0.0, 0.05, default=0.0, decimals=3, space="sell", optimize=True
    )
    exit_time1_hours = IntParameter(4, 12, default=7, space="sell", optimize=True)
    exit_time1_profit = DecimalParameter(
        -0.10, 0.0, default=-0.05, decimals=3, space="sell", optimize=True
    )
    exit_time2_hours = IntParameter(8, 16, default=10, space="sell", optimize=True)
    exit_time2_profit = DecimalParameter(
        -0.15, 0.0, default=-0.10, decimals=3, space="sell", optimize=True
    )

    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 96,
            },
            {
                "method": "MaxDrawdownProtection",
                "max_allowed_drawdown": 0.05,
                "lookback_period_candles": 288,
                "stop_duration_candles": 96,
                "trade_limit": 5,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 288,
                "trade_limit": 3,
                "stop_duration_candles": 96,
                "only_per_pair": False,
            },
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

        # New filters
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)

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

        if self.use_trend_filter.value:
            conditions &= dataframe["close"] > dataframe["ema_200"]

        if self.use_volume_filter.value:
            conditions &= dataframe["volume"] > dataframe["volume_sma"]

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

        # Relative to entry price, not current rate
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

        if current_profit > self.exit_profit_fastk.value:
            if current_candle["fastk"] > self.sell_fastx.value:
                return "fastk_profit_sell"

        if self.exit_loss_cci_profit_min.value < current_profit < self.exit_loss_cci_profit_max.value:
            if current_candle["cci"] > self.exit_loss_cci_threshold.value:
                return "cci_loss_sell"

        if current_time - timedelta(hours=self.exit_time1_hours.value) > trade.open_date_utc:
            if current_profit >= self.exit_time1_profit.value:
                return "time_loss_sell_1"

        if current_time - timedelta(hours=self.exit_time2_hours.value) > trade.open_date_utc:
            if current_profit >= self.exit_time2_profit.value:
                return "time_loss_sell_2"

        return None

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_tag"]] = (0, "long_out")
        return dataframe
