"""
FreqAI Strategy v5 — Coinglass derivatives as model features + signal logic.

Key change from v1-v4: Coinglass data is merged in feature_engineering_standard()
so LightGBM trains directly on derivatives features. This gives the model the
ability to learn correlations between funding rate, OI, taker pressure, etc.
and future price returns.

Additionally, Coinglass data drives:
  - Entry signal: derivatives score confirms direction
  - Exit signal: OI crash detection triggers emergency exit
"""

import logging
from functools import reduce

import numpy as np
import pandas as pd
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


class FreqaiExampleStrategy_v5(IStrategy):
    """
    FreqAI strategy v5 — Coinglass derivatives as FreqAI model features.
    """

    INTERFACE_VERSION = 3
    minimal_roi = {"0": 0.1, "240": -1}
    process_only_new_candles = True
    stoploss = -0.05
    use_exit_signal = True
    startup_candle_count: int = 40
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
            logger.warning("CoinglassProvider not available.")

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        # v1 proven features — unchanged
        dataframe["%-rsi-period"] = ta.RSI(dataframe, timeperiod=period)
        dataframe["%-mfi-period"] = ta.MFI(dataframe, timeperiod=period)
        dataframe["%-adx-period"] = ta.ADX(dataframe, timeperiod=period)
        dataframe["%-sma-period"] = ta.SMA(dataframe, timeperiod=period)
        dataframe["%-ema-period"] = ta.EMA(dataframe, timeperiod=period)

        bollinger = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=period, stds=2.2
        )
        dataframe["bb_lowerband-period"] = bollinger["lower"]
        dataframe["bb_middleband-period"] = bollinger["mid"]
        dataframe["bb_upperband-period"] = bollinger["upper"]

        dataframe["%-bb_width-period"] = (
            dataframe["bb_upperband-period"] - dataframe["bb_lowerband-period"]
        ) / dataframe["bb_middleband-period"]
        dataframe["%-close-bb_lower-period"] = (
            dataframe["close"] / dataframe["bb_lowerband-period"]
        )

        dataframe["%-roc-period"] = ta.ROC(dataframe, timeperiod=period)
        dataframe["%-relative_volume-period"] = (
            dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        )

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

        # --- Coinglass derivatives as model features ---
        if self.cg is not None:
            dataframe = self._add_coinglass_features(dataframe, metadata)

        return dataframe

    def _add_coinglass_features(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        symbol = metadata["pair"].split("/")[0]
        cg_df = self.cg.get_features(symbol, interval="1h")
        if cg_df.empty:
            return dataframe

        cg_df = cg_df.copy().reset_index()
        cg_df["date"] = pd.to_datetime(cg_df["date"], utc=True)

        # Derived features computed on Coinglass data before merge
        if "funding_rate_funding_rate" in cg_df.columns:
            fr = cg_df["funding_rate_funding_rate"]
            fr_mean = fr.rolling(96).mean()
            fr_std = fr.rolling(96).std()
            cg_df["funding_zscore"] = (fr - fr_mean) / fr_std.replace(0, np.nan)

        if "open_interest_open_interest" in cg_df.columns:
            oi = cg_df["open_interest_open_interest"]
            cg_df["oi_pct_change"] = oi.pct_change()
            oi_mean = oi.rolling(96).mean()
            oi_std = oi.rolling(96).std()
            cg_df["oi_zscore"] = (oi - oi_mean) / oi_std.replace(0, np.nan)

        if "taker_volume_taker_buy_volume" in cg_df.columns and \
           "taker_volume_taker_sell_volume" in cg_df.columns:
            buy = cg_df["taker_volume_taker_buy_volume"]
            sell = cg_df["taker_volume_taker_sell_volume"]
            total = buy + sell
            cg_df["taker_buy_ratio"] = buy / total.replace(0, np.nan)
            cg_df["taker_pressure"] = buy - sell

        if "liquidation_liquidation_long" in cg_df.columns and \
           "liquidation_liquidation_short" in cg_df.columns:
            long_liq = cg_df["liquidation_liquidation_long"]
            short_liq = cg_df["liquidation_liquidation_short"]
            total_liq = long_liq + short_liq
            cg_df["liq_imbalance"] = (long_liq - short_liq) / total_liq.replace(0, np.nan)

        if "long_short_ratio_long_short_ratio" in cg_df.columns:
            lsr = cg_df["long_short_ratio_long_short_ratio"]
            cg_df["lsr_change"] = lsr.pct_change()

        # Select columns to merge as FreqAI features (%-cg_ prefix)
        feature_map = {
            "funding_rate_funding_rate": "%-cg_funding_rate",
            "open_interest_open_interest": "%-cg_oi",
            "oi_pct_change": "%-cg_oi_pct_change",
            "funding_zscore": "%-cg_funding_zscore",
            "oi_zscore": "%-cg_oi_zscore",
            "taker_buy_ratio": "%-cg_taker_buy_ratio",
            "taker_pressure": "%-cg_taker_pressure",
            "liq_imbalance": "%-cg_liq_imbalance",
            "long_short_ratio_long_short_ratio": "%-cg_lsr",
            "lsr_change": "%-cg_lsr_change",
        }

        merge_cols = ["date"]
        rename_map = {}
        for src, dst in feature_map.items():
            if src in cg_df.columns:
                merge_cols.append(src)
                rename_map[src] = dst

        cg_subset = cg_df[merge_cols].rename(columns=rename_map)
        cg_subset = cg_subset.drop_duplicates(subset=["date"]).sort_values("date")

        # Left merge on date, forward-fill for 15m candles aligned with 1h data
        dataframe["_date"] = pd.to_datetime(dataframe["date"], utc=True)
        merged = dataframe.merge(
            cg_subset, left_on="_date", right_on="date",
            how="left", suffixes=("", "_cg"),
        )
        merged = merged.drop(columns=["_date"])
        if "date_cg" in merged.columns:
            merged = merged.drop(columns=["date_cg"])

        # Forward-fill Coinglass columns
        cg_feature_cols = [c for c in merged.columns if c.startswith("%-cg_")]
        merged[cg_feature_cols] = merged[cg_feature_cols].ffill().bfill()

        return merged

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)

        # After FreqAI: compute signal-level Coinglass features (not model features)
        if self.cg is not None:
            symbol = metadata["pair"].split("/")[0]
            dataframe = self.cg.merge_into(dataframe, symbol, interval="1h")
            dataframe = self._add_signal_features(dataframe)

        return dataframe

    def _add_signal_features(self, df: DataFrame) -> DataFrame:
        """Compute Coinglass features for signal logic (not model training)."""
        if "funding_rate_funding_rate" in df.columns:
            fr = df["funding_rate_funding_rate"]
            df["cg_funding_zscore"] = (fr - fr.rolling(96).mean()) / fr.rolling(96).std().replace(0, np.nan)

        if "open_interest_open_interest" in df.columns:
            oi = df["open_interest_open_interest"]
            df["cg_oi_pct_change"] = oi.pct_change()

        if "taker_volume_taker_buy_volume" in df.columns and \
           "taker_volume_taker_sell_volume" in df.columns:
            buy = df["taker_volume_taker_buy_volume"]
            sell = df["taker_volume_taker_sell_volume"]
            total = buy + sell
            df["cg_taker_buy_ratio"] = buy / total.replace(0, np.nan)

        if "liquidation_liquidation_long" in df.columns and \
           "liquidation_liquidation_short" in df.columns:
            long_liq = df["liquidation_liquidation_long"]
            short_liq = df["liquidation_liquidation_short"]
            total_liq = long_liq + short_liq
            df["cg_liq_imbalance"] = (long_liq - short_liq) / total_liq.replace(0, np.nan)

        return df

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["&-s_close"] = (
            dataframe["close"]
            .shift(-self.freqai_info["feature_parameters"]["label_period_candles"])
            .rolling(self.freqai_info["feature_parameters"]["label_period_candles"])
            .mean() / dataframe["close"] - 1
        )
        return dataframe

    def _derivatives_score(self, df: pd.Series) -> float:
        """Compute a derivatives conviction score. Positive = bullish, negative = bearish."""
        score = 0.0
        fz = df.get("cg_funding_zscore", 0)
        if pd.notna(fz):
            # Negative funding (cheap to be long) → bullish signal
            score -= fz * 0.3
        oi_ch = df.get("cg_oi_pct_change", 0)
        if pd.notna(oi_ch):
            # Rising OI → trend confirmation
            score += oi_ch * 10
        tbr = df.get("cg_taker_buy_ratio", 0.5)
        if pd.notna(tbr):
            # More buying than selling → bullish
            score += (tbr - 0.5) * 2
        liq = df.get("cg_liq_imbalance", 0)
        if pd.notna(liq):
            # More short liquidations → bullish (forced buying)
            score -= liq * 0.5
        return score

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        has_cg = "cg_funding_zscore" in df.columns

        enter_long_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] > 0.01,
        ]

        if has_cg:
            # Derivatives must confirm bullish direction
            ds_long = df[["cg_funding_zscore", "cg_oi_pct_change",
                          "cg_taker_buy_ratio", "cg_liq_imbalance"]].fillna(0)
            scores = ds_long.apply(self._derivatives_score, axis=1)
            enter_long_conditions.append(scores > -0.5)

        if enter_long_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_long_conditions),
                ["enter_long", "enter_tag"],
            ] = (1, "long")

        enter_short_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] < -0.01,
        ]

        if has_cg:
            enter_short_conditions.append(scores < 0.5)

        if enter_short_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_short_conditions),
                ["enter_short", "enter_tag"],
            ] = (1, "short")

        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        exit_long_conditions = [df["do_predict"] == 1, df["&-s_close"] < 0]

        # Emergency exit: OI crash (market unwinding)
        if "cg_oi_pct_change" in df.columns:
            exit_long_conditions.append(
                df["cg_oi_pct_change"].fillna(0) > -0.05
            )

        if exit_long_conditions:
            df.loc[reduce(lambda x, y: x & y, exit_long_conditions), "exit_long"] = 1

        exit_short_conditions = [df["do_predict"] == 1, df["&-s_close"] > 0]

        if "cg_oi_pct_change" in df.columns:
            exit_short_conditions.append(
                df["cg_oi_pct_change"].fillna(0) > -0.05
            )

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
