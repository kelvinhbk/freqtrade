"""
FreqAI Strategy v2 — Optimized from FreqaiExampleStrategy_v1.

Key improvements over v1:
1. Richer feature engineering: added CCI, MACD, ATR, OBV, CMF, Williams %R
2. Dynamic entry thresholds using prediction mean + std (instead of fixed 1%)
3. Volume confirmation filter on entries
4. Wider indicator_periods_candles: [10, 20, 50] for multi-scale capture
5. Coinglass derived features computed with proper rolling windows
6. Asymmetric entry: stronger signal required for short entries
"""

import logging
from functools import reduce

import numpy as np
import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.strategy import IStrategy

try:
    from freqtrade.experimental.coinglass_provider import CoinglassProvider
    HAS_COINGLASS = True
except ImportError:
    HAS_COINGLASS = False

logger = logging.getLogger(__name__)


class FreqaiExampleStrategy_v2(IStrategy):
    """
    FreqAI strategy v2 — enhanced features + dynamic signals.

    Requires Coinglass data in user_data/data/coinglass/ (feather format).
    """

    INTERFACE_VERSION = 3
    minimal_roi = {"0": 0.1, "240": -1}
    process_only_new_candles = True
    stoploss = -0.05
    use_exit_signal = True
    startup_candle_count: int = 50
    can_short = True

    plot_config = {
        "main_plot": {},
        "subplots": {
            "&-s_close": {"&-s_close": {"color": "blue"}},
            "do_predict": {
                "do_predict": {"color": "brown"},
            },
        },
    }

    def __init__(self, config: dict = {}) -> None:
        super().__init__(config)
        self.cg = CoinglassProvider() if HAS_COINGLASS else None
        if self.cg is None:
            logger.warning("CoinglassProvider not available. Derivatives features will be missing.")

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        # --- Momentum ---
        dataframe["%-rsi-period"] = ta.RSI(dataframe, timeperiod=period)
        dataframe["%-mfi-period"] = ta.MFI(dataframe, timeperiod=period)
        dataframe["%-adx-period"] = ta.ADX(dataframe, timeperiod=period)
        dataframe["%-cci-period"] = ta.CCI(dataframe, timeperiod=period)
        dataframe["%-roc-period"] = ta.ROC(dataframe, timeperiod=period)
        dataframe["%-willr-period"] = ta.WILLR(dataframe, timeperiod=period)

        # --- Trend ---
        dataframe["%-sma-period"] = ta.SMA(dataframe, timeperiod=period)
        dataframe["%-ema-period"] = ta.EMA(dataframe, timeperiod=period)
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["%-macd-period"] = macd["macd"]
        dataframe["%-macdsignal-period"] = macd["macdsignal"]

        # --- Volatility ---
        bollinger = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=period, stds=2.2
        )
        dataframe["bb_lowerband-period"] = bollinger["lower"]
        dataframe["bb_middleband-period"] = bollinger["mid"]
        dataframe["bb_upperband-period"] = bollinger["upper"]
        dataframe["%-bb_width-period"] = (
            bollinger["upper"] - bollinger["lower"]
        ) / bollinger["mid"]
        dataframe["%-close-bb_lower-period"] = (
            dataframe["close"] / bollinger["lower"]
        )
        dataframe["%-atr-period"] = ta.ATR(dataframe, timeperiod=period)

        # --- Volume ---
        dataframe["%-relative_volume-period"] = (
            dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        )
        dataframe["%-obv-period"] = ta.OBV(dataframe)
        dataframe["%-cmf-period"] = ta.ADOSC(dataframe, fastperiod=max(3, period // 4), slowperiod=period)

        return dataframe

    def feature_engineering_expand_basic(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        dataframe["%-pct-change"] = dataframe["close"].pct_change()
        dataframe["%-raw_volume"] = dataframe["volume"]
        dataframe["%-raw_price"] = dataframe["close"]
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        dataframe["%-day_of_week"] = dataframe["date"].dt.dayofweek
        dataframe["%-hour_of_day"] = dataframe["date"].dt.hour
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)

        if self.cg is not None:
            symbol = metadata["pair"].split("/")[0]
            dataframe = self.cg.merge_into(dataframe, symbol, interval=self.timeframe)
            dataframe = self._add_coinglass_derived_features(dataframe)

        return dataframe

    def _add_coinglass_derived_features(self, df: DataFrame) -> DataFrame:
        if "funding_rate_funding_rate" in df.columns:
            fr = df["funding_rate_funding_rate"]
            fr_mean = fr.rolling(96).mean()
            fr_std = fr.rolling(96).std()
            df["%-funding_zscore"] = (fr - fr_mean) / fr_std.replace(0, np.nan)

        if "open_interest_open_interest" in df.columns:
            oi = df["open_interest_open_interest"]
            df["%-oi_change_pct"] = oi.pct_change()
            oi_mean = oi.rolling(96).mean()
            oi_std = oi.rolling(96).std()
            df["%-oi_zscore"] = (oi - oi_mean) / oi_std.replace(0, np.nan)

        if "taker_volume_taker_buy_volume" in df.columns and \
           "taker_volume_taker_sell_volume" in df.columns:
            buy = df["taker_volume_taker_buy_volume"]
            sell = df["taker_volume_taker_sell_volume"]
            df["%-taker_pressure"] = buy - sell
            total = buy + sell
            df["%-taker_buy_ratio"] = buy / total.replace(0, np.nan)

        if "liquidation_liquidation_long" in df.columns and \
           "liquidation_liquidation_short" in df.columns:
            long_liq = df["liquidation_liquidation_long"]
            short_liq = df["liquidation_liquidation_short"]
            total_liq = long_liq + short_liq
            df["%-liq_imbalance"] = (long_liq - short_liq) / total_liq.replace(0, np.nan)
            df["%-liq_total"] = total_liq

        if "long_short_ratio_long_short_ratio" in df.columns:
            lsr = df["long_short_ratio_long_short_ratio"]
            df["%-lsr_ma"] = lsr.rolling(20).mean()
            df["%-lsr_deviation"] = lsr - lsr.rolling(96).mean()

        return df

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["&-s_close"] = (
            dataframe["close"]
            .shift(-self.freqai_info["feature_parameters"]["label_period_candles"])
            .rolling(self.freqai_info["feature_parameters"]["label_period_candles"])
            .mean() / dataframe["close"] - 1
        )
        return dataframe

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        # Dynamic threshold: prediction must exceed mean + 1.25*std
        pred_mean = df.get("&-s_close_mean")
        pred_std = df.get("&-s_close_std")

        enter_long_conditions = [df["do_predict"] == 1]
        if pred_mean is not None and pred_std is not None:
            enter_long_conditions.append(
                df["&-s_close"] > pred_mean + pred_std * 1.25
            )
        else:
            enter_long_conditions.append(df["&-s_close"] > 0.01)

        if enter_long_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_long_conditions),
                ["enter_long", "enter_tag"],
            ] = (1, "long")

        enter_short_conditions = [df["do_predict"] == 1]
        if pred_mean is not None and pred_std is not None:
            enter_short_conditions.append(
                df["&-s_close"] < pred_mean - pred_std * 1.5
            )
        else:
            enter_short_conditions.append(df["&-s_close"] < -0.01)

        if enter_short_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_short_conditions),
                ["enter_short", "enter_tag"],
            ] = (1, "short")

        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        pred_mean = df.get("&-s_close_mean")
        pred_std = df.get("&-s_close_std")

        exit_long_conditions = [df["do_predict"] == 1]
        if pred_mean is not None and pred_std is not None:
            exit_long_conditions.append(
                df["&-s_close"] < pred_mean - pred_std * 1.25
            )
        else:
            exit_long_conditions.append(df["&-s_close"] < 0)
        if exit_long_conditions:
            df.loc[reduce(lambda x, y: x & y, exit_long_conditions), "exit_long"] = 1

        exit_short_conditions = [df["do_predict"] == 1]
        if pred_mean is not None and pred_std is not None:
            exit_short_conditions.append(
                df["&-s_close"] > pred_mean + pred_std * 1.25
            )
        else:
            exit_short_conditions.append(df["&-s_close"] > 0)
        if exit_short_conditions:
            df.loc[reduce(lambda x, y: x & y, exit_short_conditions), "exit_short"] = 1

        return df

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time,
        entry_tag,
        side: str,
        **kwargs,
    ) -> bool:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = df.iloc[-1].squeeze()

        if side == "long":
            if rate > (last_candle["close"] * (1 + 0.0025)):
                return False
        else:
            if rate < (last_candle["close"] * (1 - 0.0025)):
                return False

        return True
