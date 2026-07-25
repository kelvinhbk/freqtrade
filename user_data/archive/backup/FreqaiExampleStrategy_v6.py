"""
FreqAI Strategy v6 — BTC+ETH + dual-interval Coinglass + Kelly sizing + 1x leverage.

Changes from v5:
  - Add 15m Coinglass data alongside 1h for microstructure signals
  - Kelly criterion dynamic position sizing based on signal confidence
  - Real-time Coinglass data fetching in live mode
  - Drawdown protection in position sizing
"""

import logging
from datetime import datetime
from functools import reduce

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.strategy import DecimalParameter, IStrategy, IntParameter

try:
    from freqtrade.experimental.coinglass_provider import CoinglassProvider
    HAS_COINGLASS = True
except ImportError:
    HAS_COINGLASS = False

logger = logging.getLogger(__name__)


class FreqaiExampleStrategy_v6(IStrategy):
    """
    FreqAI strategy v6 — BTC+ETH, dual-interval Coinglass, Kelly sizing, 1x leverage.
    """

    INTERFACE_VERSION = 3
    minimal_roi = {"0": 0.1, "240": -1}
    process_only_new_candles = True
    stoploss = -0.05
    trailing_stop = False
    use_exit_signal = True
    startup_candle_count: int = 40
    can_short = True
    position_adjustment_enable = False

    # Kelly position sizing bounds (multiplier on base_stake)
    MIN_KELLY_FRACTION = 0.5   # 0.5x base stake (low confidence)
    MAX_KELLY_FRACTION = 1.0   # 1.0x base stake (high confidence)
    TARGET_LEVERAGE = 1.0

    # Hyperopt parameters
    buy_pred_threshold = DecimalParameter(0.005, 0.03, default=0.01, decimals=3, space="buy")
    sell_pred_threshold = DecimalParameter(0.005, 0.03, default=0.01, decimals=3, space="sell")

    # Derivatives score weights
    funding_weight_1h = DecimalParameter(0.1, 1.0, default=0.3, decimals=1, space="buy")
    oi_weight_1h = DecimalParameter(1.0, 20.0, default=10.0, decimals=0, space="buy")
    taker_weight_1h = DecimalParameter(0.5, 5.0, default=2.0, decimals=1, space="buy")
    funding_weight_15m = DecimalParameter(0.05, 0.5, default=0.2, decimals=2, space="buy")
    oi_weight_15m = DecimalParameter(1.0, 15.0, default=5.0, decimals=0, space="buy")
    taker_weight_15m = DecimalParameter(0.2, 3.0, default=1.0, decimals=1, space="buy")

    # Entry score threshold
    entry_score_threshold = DecimalParameter(-1.0, 1.0, default=-0.5, decimals=1, space="buy")

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

    # ------------------------------------------------------------------
    # FreqAI feature engineering
    # ------------------------------------------------------------------

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
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

        if self.cg is not None:
            dataframe = self._add_coinglass_features(dataframe, metadata)
            # Fill NaN for Coinglass features to prevent FreqAI from dropping
            # all training data when Coinglass coverage is sparse.
            cg_cols = [c for c in dataframe.columns if c.startswith("%-cg_")]
            if cg_cols:
                dataframe[cg_cols] = dataframe[cg_cols].ffill().bfill().fillna(0)

        return dataframe

    def _add_coinglass_features(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        symbol = metadata["pair"].split("/")[0]

        for interval, prefix in [("1h", "%-cg_1h_"), ("15m", "%-cg_15m_")]:
            cg_df = self.cg.get_features(symbol, interval=interval)
            if cg_df.empty:
                continue
            dataframe = self._merge_cg_interval(dataframe, cg_df, prefix, interval)

        return dataframe

    def _merge_cg_interval(
        self, dataframe: DataFrame, cg_df: pd.DataFrame, prefix: str, interval: str
    ) -> DataFrame:
        cg_df = cg_df.copy().reset_index()
        cg_df["date"] = pd.to_datetime(cg_df["date"], utc=True)

        rolling_window = 96 if interval == "1h" else 384

        if "funding_rate_funding_rate" in cg_df.columns:
            fr = cg_df["funding_rate_funding_rate"]
            fr_mean = fr.rolling(rolling_window).mean()
            fr_std = fr.rolling(rolling_window).std()
            cg_df["funding_zscore"] = (fr - fr_mean) / fr_std.replace(0, np.nan)

        if "open_interest_open_interest" in cg_df.columns:
            oi = cg_df["open_interest_open_interest"]
            cg_df["oi_pct_change"] = oi.pct_change(fill_method=None)
            oi_mean = oi.rolling(rolling_window).mean()
            oi_std = oi.rolling(rolling_window).std()
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
            cg_df["lsr_change"] = lsr.pct_change(fill_method=None)

        feature_map = {
            "funding_rate_funding_rate": f"{prefix}funding_rate",
            "open_interest_open_interest": f"{prefix}oi",
            "oi_pct_change": f"{prefix}oi_pct_change",
            "funding_zscore": f"{prefix}funding_zscore",
            "oi_zscore": f"{prefix}oi_zscore",
            "taker_buy_ratio": f"{prefix}taker_buy_ratio",
            "taker_pressure": f"{prefix}taker_pressure",
            "liq_imbalance": f"{prefix}liq_imbalance",
            "long_short_ratio_long_short_ratio": f"{prefix}lsr",
            "lsr_change": f"{prefix}lsr_change",
        }

        merge_cols = ["date"]
        rename_map = {}
        for src, dst in feature_map.items():
            if src in cg_df.columns:
                merge_cols.append(src)
                rename_map[src] = dst

        cg_subset = cg_df[merge_cols].rename(columns=rename_map)
        cg_subset = cg_subset.drop_duplicates(subset=["date"]).sort_values("date")

        dataframe["_date"] = pd.to_datetime(dataframe["date"], utc=True)
        merged = dataframe.merge(
            cg_subset, left_on="_date", right_on="date",
            how="left", suffixes=("", "_cg"),
        )
        merged = merged.drop(columns=["_date"])
        if "date_cg" in merged.columns:
            merged = merged.drop(columns=["date_cg"])

        cg_feature_cols = [c for c in merged.columns if c.startswith(prefix)]
        merged[cg_feature_cols] = merged[cg_feature_cols].ffill().bfill()

        return merged

    # ------------------------------------------------------------------
    # Indicators & targets
    # ------------------------------------------------------------------

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)

        if self.cg is not None:
            symbol = metadata["pair"].split("/")[0]
            for interval, suffix in [("1h", ""), ("15m", "_15m")]:
                dataframe = self.cg.merge_into(dataframe, symbol, interval=interval)
                dataframe = self._add_signal_features(dataframe, suffix, interval)

        return dataframe

    def _add_signal_features(self, df: DataFrame, suffix: str, interval: str) -> DataFrame:
        rolling_window = 96 if interval == "1h" else 384

        if "funding_rate_funding_rate" in df.columns:
            fr = df["funding_rate_funding_rate"]
            df[f"cg{suffix}_funding_zscore"] = (
                (fr - fr.rolling(rolling_window).mean())
                / fr.rolling(rolling_window).std().replace(0, np.nan)
            )

        if "open_interest_open_interest" in df.columns:
            oi = df["open_interest_open_interest"]
            df[f"cg{suffix}_oi_pct_change"] = oi.pct_change(fill_method=None)

        if "taker_volume_taker_buy_volume" in df.columns and \
           "taker_volume_taker_sell_volume" in df.columns:
            buy = df["taker_volume_taker_buy_volume"]
            sell = df["taker_volume_taker_sell_volume"]
            total = buy + sell
            df[f"cg{suffix}_taker_buy_ratio"] = buy / total.replace(0, np.nan)

        if "liquidation_liquidation_long" in df.columns and \
           "liquidation_liquidation_short" in df.columns:
            long_liq = df["liquidation_liquidation_long"]
            short_liq = df["liquidation_liquidation_short"]
            total_liq = long_liq + short_liq
            df[f"cg{suffix}_liq_imbalance"] = (
                (long_liq - short_liq) / total_liq.replace(0, np.nan)
            )

        return df

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["&-s_close"] = (
            dataframe["close"]
            .shift(-self.freqai_info["feature_parameters"]["label_period_candles"])
            .rolling(self.freqai_info["feature_parameters"]["label_period_candles"])
            .mean() / dataframe["close"] - 1
        )
        return dataframe

    # ------------------------------------------------------------------
    # Signal logic
    # ------------------------------------------------------------------

    def _derivatives_score(self, row: pd.Series) -> float:
        score = 0.0

        # 1h macro signals (higher weight)
        fz_1h = row.get("cg_funding_zscore", 0)
        if pd.notna(fz_1h):
            score -= fz_1h * self.funding_weight_1h.value
        oi_1h = row.get("cg_oi_pct_change", 0)
        if pd.notna(oi_1h):
            score += oi_1h * self.oi_weight_1h.value
        tbr_1h = row.get("cg_taker_buy_ratio", 0.5)
        if pd.notna(tbr_1h):
            score += (tbr_1h - 0.5) * self.taker_weight_1h.value
        liq_1h = row.get("cg_liq_imbalance", 0)
        if pd.notna(liq_1h):
            score -= liq_1h * 0.5

        # 15m microstructure signals (lower weight)
        fz_15m = row.get("cg_15m_funding_zscore", 0)
        if pd.notna(fz_15m):
            score -= fz_15m * self.funding_weight_15m.value
        oi_15m = row.get("cg_15m_oi_pct_change", 0)
        if pd.notna(oi_15m):
            score += oi_15m * self.oi_weight_15m.value
        tbr_15m = row.get("cg_15m_taker_buy_ratio", 0.5)
        if pd.notna(tbr_15m):
            score += (tbr_15m - 0.5) * self.taker_weight_15m.value
        liq_15m = row.get("cg_15m_liq_imbalance", 0)
        if pd.notna(liq_15m):
            score -= liq_15m * 0.3

        return score

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        has_cg = "cg_funding_zscore" in df.columns

        enter_long_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] > self.buy_pred_threshold.value,
        ]

        if has_cg:
            scores = df.apply(self._derivatives_score, axis=1)
            enter_long_conditions.append(scores > self.entry_score_threshold.value)

        if enter_long_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_long_conditions),
                ["enter_long", "enter_tag"],
            ] = (1, "long")

        enter_short_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] < -self.sell_pred_threshold.value,
        ]

        if has_cg:
            enter_short_conditions.append(scores < -self.entry_score_threshold.value)

        if enter_short_conditions:
            df.loc[
                reduce(lambda x, y: x & y, enter_short_conditions),
                ["enter_short", "enter_tag"],
            ] = (1, "short")

        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        exit_long_conditions = [df["do_predict"] == 1, df["&-s_close"] < 0]

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

    # ------------------------------------------------------------------
    # Leverage & position sizing
    # ------------------------------------------------------------------

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return min(self.TARGET_LEVERAGE, max_leverage)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        # Base stake = available balance / max open trades
        base_stake = max_stake
        if self.wallets is not None:
            available = self.wallets.get_available_stake_amount()
            max_trades = self.config.get("max_open_trades", 3)
            base_stake = available / max(max_trades, 1)

        # Kelly confidence multiplier based on prediction strength
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        kelly_fraction = (self.MIN_KELLY_FRACTION + self.MAX_KELLY_FRACTION) / 2  # default 0.75

        if len(df) > 0:
            last = df.iloc[-1]
            pred_return = abs(last.get("&-s_close", 0))
            if isinstance(pred_return, pd.Series):
                pred_return = float(pred_return.iloc[0])

            deriv_score = abs(self._derivatives_score(last))

            # Simple confidence: higher prediction + derivative confirmation → larger fraction
            confidence = min(1.0, pred_return * 10 + deriv_score * 0.1)
            kelly_fraction = self.MIN_KELLY_FRACTION + confidence * (
                self.MAX_KELLY_FRACTION - self.MIN_KELLY_FRACTION
            )

        stake = base_stake * kelly_fraction

        # Reduce position during drawdown
        if self.wallets is not None:
            total = self.wallets.get_total_stake_amount()
            available = self.wallets.get_available_stake_amount()
            if total > 0:
                drawdown_ratio = 1 - (available / total)
                if drawdown_ratio > 0.05:
                    stake *= max(0.3, 1 - drawdown_ratio)

        if min_stake is not None:
            stake = max(min_stake, stake)
        stake = min(stake, max_stake)

        return stake

    use_custom_stoploss = False
    # ------------------------------------------------------------------

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
