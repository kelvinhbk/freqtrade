"""
MACD Histogram Divergence Strategy V5

核心逻辑:
  - 检测 MACD histogram 与价格的背离 (order=1 局部极值)
  - 底背离做多: 价格持续创新低, MACD 波谷持续抬高
  - 顶背离做空: 价格持续创新高, MACD 波峰持续降低
  - 关键K线: 背离后第一根满足 dark→light 转换的 K 线
      (多单要求 hist < 0, 空单要求 hist > 0)
  - 入场: 关键K线收盘时入场 (下一根 K 线开盘价)
  - 出场: 统一 chandelier ATR 追踪止损 + 最大持仓时间
  - 无 DCA, 无分批止盈
  - 杠杆: 2x

与 macd_v4 的核心差异:
  1. 移除 TP1 分批止盈和两阶段止损复杂度
  2. 统一 chandelier ATR 追踪止损 (动态 N 倍 ATR)
  3. 添加最大持仓时间退出 (防止盈利变亏损)
  4. ATR 倍数根据波动率状态自适应调整
  5. 无 entry filters, no trend filters, no volume gates
"""

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    stoploss_from_absolute,
)
from pandas import DataFrame
import talib.abstract as ta
import numpy as np
from scipy.signal import argrelextrema


class macd_v4_E0009(IStrategy):

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = '15m'

    # ── 常量 ────────────────────────────────────────────────────────────────────

    _TAG_BULL_SINGLE = 'bull_single'
    _TAG_BULL_CONT   = 'bull_cont'
    _TAG_BEAR_SINGLE = 'bear_single'
    _TAG_BEAR_CONT   = 'bear_cont'

    _KEY_ENTRY_PRICE = 'entry_price'
    _KEY_ENTRY_ATR   = 'entry_atr'
    _KEY_ENTRY_HH    = 'entry_hh'  # highest high for long trail
    _KEY_ENTRY_LL    = 'entry_ll'  # lowest low for short trail

    # 兜底 ROI (实际通过 custom_stoploss 出场)
    minimal_roi = {"0": 1.00}  # effectively disabled, let stoploss handle

    # 兜底止损 (会被 custom_stoploss 覆盖)
    stoploss = -0.50  # wide fallback
    use_custom_stoploss = True
    trailing_stop = False
    position_adjustment_enable = False  # disable partial exits

    # 启动需要足够历史数据
    startup_candle_count = 100

    # ── 固定规格参数 ──────────────────────────────────────────────────────────

    MACD_FAST: int = 13
    MACD_SLOW: int = 34
    MACD_SIGNAL: int = 9
    ATR_PERIOD: int = 13

    # ── Hyperopt 参数 ──────────────────────────────────────────────────────────

    # 单次背离强度阈值
    min_single_strength = DecimalParameter(0.15, 0.55, default=0.372, space='buy', optimize=True)

    # 连续背离总强度阈值
    min_cont_strength = DecimalParameter(0.35, 0.85, default=0.618, space='buy', optimize=True)

    # Chandelier ATR 倍数 (基础)
    atr_mult_base = DecimalParameter(1.5, 4.0, default=2.5, space='sell', optimize=True)

    # Chandelier ATR 倍数 (波动率扩张时, 更宽)
    atr_mult_vol = DecimalParameter(2.0, 5.0, default=3.5, space='sell', optimize=True)

    # 波动率扩张阈值: current_ATR / entry_ATR > vol_ratio 视为高波动
    vol_ratio = DecimalParameter(1.2, 2.5, default=1.5, space='sell', optimize=True)

    # 最大持仓时间 (小时)
    max_hold_hours = IntParameter(4, 72, default=24, space='sell', optimize=True)

    # 最小盈利保护: 当 profit > profit_lock_pct 时, 止损至少锁定到 profit_lock_sl
    profit_lock_pct = DecimalParameter(0.02, 0.10, default=0.05, space='sell', optimize=True)
    profit_lock_sl = DecimalParameter(0.01, 0.05, default=0.02, space='sell', optimize=True)

    # 仓位比例
    stake_single = DecimalParameter(0.15, 0.35, default=0.20, space='buy', optimize=False)
    stake_cont = DecimalParameter(0.20, 0.45, default=0.30, space='buy', optimize=False)

    # ── 指标计算 ───────────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD histogram
        macd = ta.MACD(
            dataframe,
            fastperiod=self.MACD_FAST,
            slowperiod=self.MACD_SLOW,
            signalperiod=self.MACD_SIGNAL,
        )
        dataframe['macdhist'] = macd['macdhist']

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.ATR_PERIOD)

        # ATR 趋势 (用于波动率状态判断)
        dataframe['atr_sma'] = ta.SMA(dataframe['atr'], timeperiod=self.ATR_PERIOD * 2)

        # 深浅色柱子识别
        h = dataframe['macdhist']
        prev_h = h.shift(1)
        dataframe['hist_dark'] = ((h > 0) & (h > prev_h)) | ((h < 0) & (h < prev_h))
        dataframe['hist_light'] = ((h > 0) & (h < prev_h)) | ((h < 0) & (h > prev_h))

        # 深转浅
        dataframe['dark_to_light'] = dataframe['hist_dark'].shift(1) & dataframe['hist_light']

        # 背离检测 + 关键K线标记
        dataframe = self._detect_divergence(dataframe, is_long=True)
        dataframe = self._detect_divergence(dataframe, is_long=False)

        return dataframe

    # ── 背离检测辅助 ───────────────────────────────────────────────────────────

    @staticmethod
    def _calc_strength(curr_val: float, prev_val: float) -> float:
        if abs(prev_val) < 1e-10:
            return 0.0
        return abs(curr_val - prev_val) / abs(prev_val)

    def _find_key_candle(
        self,
        hist: np.ndarray,
        dtl: np.ndarray,
        extremum_idx: int,
        is_long: bool,
    ) -> int | None:
        n = len(hist)
        for offset in range(1, 5):
            cand = extremum_idx + offset
            if cand >= n:
                return None
            in_zone = (hist[cand] < 0) if is_long else (hist[cand] > 0)
            if dtl[cand] and in_zone:
                return cand
        return None

    def _detect_divergence(self, dataframe: DataFrame, is_long: bool) -> DataFrame:
        lookback = 50
        hist = dataframe['macdhist'].values
        price = dataframe['low'].values if is_long else dataframe['high'].values
        dtl = dataframe['dark_to_light'].values
        n = len(dataframe)

        comparator = np.less if is_long else np.greater
        all_extreme_idx = argrelextrema(hist, comparator, order=1)[0]
        extreme_idx = all_extreme_idx[
            (hist[all_extreme_idx] < 0) if is_long else (hist[all_extreme_idx] > 0)
        ]

        sig_arr      = np.zeros(n, dtype=bool)
        count_arr    = np.zeros(n, dtype=np.int32)
        strength_arr = np.zeros(n, dtype=np.float64)

        for ei in extreme_idx:
            key_idx = self._find_key_candle(hist, dtl, ei, is_long=is_long)
            if key_idx is None:
                continue

            win_start = max(0, ei - lookback)
            prev_extreme = extreme_idx[(extreme_idx >= win_start) & (extreme_idx < ei)]
            if len(prev_extreme) == 0:
                continue

            consecutive = 0
            total_strength = 0.0
            cur = ei

            for prev in reversed(prev_extreme):
                hist_diverges = (hist[cur] > hist[prev]) if is_long else (hist[cur] < hist[prev])
                price_diverges = (price[cur] < price[prev]) if is_long else (price[cur] > price[prev])
                if hist_diverges and price_diverges:
                    total_strength += self._calc_strength(hist[cur], hist[prev])
                    consecutive += 1
                    cur = prev
                else:
                    break

            if consecutive == 0:
                continue

            threshold = (
                self.min_single_strength.value
                if consecutive == 1
                else self.min_cont_strength.value
            )
            if total_strength < threshold:
                continue

            sig_arr[key_idx]      = True
            count_arr[key_idx]    = consecutive
            strength_arr[key_idx] = total_strength

        prefix = 'bullish' if is_long else 'bearish'
        dataframe[f'{prefix}_signal']       = sig_arr
        dataframe[f'{prefix}_div_count']    = count_arr
        dataframe[f'{prefix}_div_strength'] = strength_arr

        return dataframe

    # ── 入场 / 出场信号 ────────────────────────────────────────────────────────

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        vol = dataframe['volume'] > 0

        dataframe.loc[
            dataframe['bullish_signal'] & (dataframe['bullish_div_count'] == 1) & vol,
            ['enter_long', 'enter_tag'],
        ] = (1, self._TAG_BULL_SINGLE)

        dataframe.loc[
            dataframe['bullish_signal'] & (dataframe['bullish_div_count'] >= 2) & vol,
            ['enter_long', 'enter_tag'],
        ] = (1, self._TAG_BULL_CONT)

        dataframe.loc[
            dataframe['bearish_signal'] & (dataframe['bearish_div_count'] == 1) & vol,
            ['enter_short', 'enter_tag'],
        ] = (1, self._TAG_BEAR_SINGLE)

        dataframe.loc[
            dataframe['bearish_signal'] & (dataframe['bearish_div_count'] >= 2) & vol,
            ['enter_short', 'enter_tag'],
        ] = (1, self._TAG_BEAR_CONT)

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 出场完全由 custom_stoploss 控制
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    # ── 仓位管理 ───────────────────────────────────────────────────────────────

    def custom_stake_amount(
        self,
        current_time,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        available = self.wallets.get_available_stake_amount()

        if entry_tag in (self._TAG_BULL_SINGLE, self._TAG_BEAR_SINGLE):
            stake = available * self.stake_single.value
        elif entry_tag in (self._TAG_BULL_CONT, self._TAG_BEAR_CONT):
            stake = available * self.stake_cont.value
        else:
            return proposed_stake

        if min_stake and stake < min_stake:
            return min_stake
        return min(stake, max_stake)

    # ── 自定义止损 (统一 chandelier ATR 追踪) ──────────────────────────────────

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """
        统一 chandelier ATR 追踪止损 + 时间退出 + 盈利保护。

        逻辑:
        1. 入场时缓存 entry_price, entry_atr
        2. 追踪最高/最低价 (hh/ll since entry)
        3. 止损价 = hh - atr_mult * current_atr (多) / ll + atr_mult * current_atr (空)
        4. atr_mult 根据波动率状态切换: base vs vol
        5. 盈利保护: 当 profit > profit_lock_pct, 止损不低于 profit_lock_sl
        6. 时间退出: 持仓超过 max_hold_hours 强制收紧到当前价附近
        """
        # 初始化缓存
        entry_price = trade.get_custom_data(self._KEY_ENTRY_PRICE)
        if entry_price is None:
            trade.set_custom_data(self._KEY_ENTRY_PRICE, trade.open_rate)
            # 获取入场时 ATR
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if not dataframe.empty:
                entry_atr = float(dataframe.iloc[-1]['atr'])
            else:
                entry_atr = 0.0
            trade.set_custom_data(self._KEY_ENTRY_ATR, entry_atr)
            entry_price = trade.open_rate

        entry_atr = trade.get_custom_data(self._KEY_ATR_ENTRY) or 0.001

        # 获取当前数据
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss

        current_candle = dataframe.iloc[-1]
        current_atr = float(current_candle['atr'])
        if np.isnan(current_atr) or current_atr <= 0:
            current_atr = entry_atr

        # 波动率状态: current_ATR vs entry_ATR
        vol_expanded = (current_atr / max(entry_atr, 0.001)) > self.vol_ratio.value
        atr_mult = self.atr_mult_vol.value if vol_expanded else self.atr_mult_base.value

        # 更新追踪最高/最低价
        if trade.is_short:
            trail_ll = trade.get_custom_data(self._KEY_ENTRY_LL)
            if trail_ll is None or current_rate < trail_ll:
                trail_ll = current_rate
                trade.set_custom_data(self._KEY_ENTRY_LL, trail_ll)
            # 空单: 止损 = ll + atr_mult * current_atr
            sl_price = trail_ll + atr_mult * current_atr
        else:
            trail_hh = trade.get_custom_data(self._KEY_ENTRY_HH)
            if trail_hh is None or current_rate > trail_hh:
                trail_hh = current_rate
                trade.set_custom_data(self._KEY_ENTRY_HH, trail_hh)
            # 多单: 止损 = hh - atr_mult * current_atr
            sl_price = trail_hh - atr_mult * current_atr

        # 时间退出: 持仓过久, 强制收紧
        hold_time = (current_time - trade.open_date_utc).total_seconds() / 3600
        if hold_time > self.max_hold_hours.value:
            # 时间退出: 移动到盈亏平衡或当前价的小回撤
            if trade.is_short:
                sl_price = max(sl_price, current_rate * 1.005)  # 空单: 当前价上方0.5%
            else:
                sl_price = min(sl_price, current_rate * 0.995)  # 多单: 当前价下方0.5%

        # 盈利保护
        profit_pct = current_profit / trade.leverage  # 实际资金收益率
        if profit_pct > self.profit_lock_pct.value:
            lock_price = entry_price * (1 + self.profit_lock_sl.value) if not trade.is_short else entry_price * (1 - self.profit_lock_sl.value)
            if trade.is_short:
                sl_price = min(sl_price, lock_price)
            else:
                sl_price = max(sl_price, lock_price)

        # 计算 stoploss 返回值
        sl = stoploss_from_absolute(
            sl_price, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

        # 限制最大亏损
        return max(sl, -0.20)

    # ── 杠杆 ───────────────────────────────────────────────────────────────────

    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str,
        side: str,
        **kwargs,
    ) -> float:
        return 2.0
