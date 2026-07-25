"""
MACD Histogram Divergence Strategy V5

核心变更 (针对近期实验失败教训):
  - 完全保留入场信号逻辑, 不添加任何趋势/成交量/入场过滤器
  - 止损改为基于入场价的对称 ATR 风险 (替代关键K线极值)
  - TP1 改为百分比利润目标 (替代固定 ATR 倍数)
  - 阶段2 追踪改为百分比回撤追踪 (替代 ATR 追踪)
  - 新增最大持仓时间退出 (防止无限期亏损持仓)
  - 所有参数均为 hyperopt 可调

失败模式规避:
  - 不减少交易机会 (iter 10-11 教训)
  - 不扩大止损/动态加宽 (iter 7-9 教训)
  - 不添加复杂过滤 (iter 8 教训)
"""

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    stoploss_from_open,
)
from pandas import DataFrame
import talib.abstract as ta
import numpy as np
from scipy.signal import argrelextrema


class macd_v4_E0012(IStrategy):

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = '15m'

    # ── 常量 ────────────────────────────────────────────────────────────────────

    _TAG_BULL_SINGLE = 'bull_single'
    _TAG_BULL_CONT   = 'bull_cont'
    _TAG_BEAR_SINGLE = 'bear_single'
    _TAG_BEAR_CONT   = 'bear_cont'

    _KEY_TP1_DONE    = 'tp1_done'
    _KEY_ENTRY_RATE  = 'entry_rate'
    _KEY_TRAIL_MAX   = 'trail_max'
    _KEY_TRAIL_MIN   = 'trail_min'
    _KEY_ENTRY_TIME  = 'entry_time'

    # 兜底 ROI (由 custom_exit 覆盖)
    minimal_roi = {"0": 1.00}

    # 硬止损上限 (最大承受亏损, 会被 custom_stoploss 收紧)
    stoploss = -0.50
    use_custom_stoploss = True
    trailing_stop = False
    position_adjustment_enable = True

    startup_candle_count = 100

    # ── 固定规格参数 ──────────────────────────────────────────────────────────

    MACD_FAST: int = 13
    MACD_SLOW: int = 34
    MACD_SIGNAL: int = 9
    ATR_PERIOD: int = 13

    # ── Hyperopt 参数 ──────────────────────────────────────────────────────────

    # 背离强度阈值 (保留原有逻辑)
    min_single_strength = DecimalParameter(0.10, 0.50, default=0.30, space='buy', optimize=True)
    min_cont_strength = DecimalParameter(0.30, 0.80, default=0.50, space='buy', optimize=True)

    # 入场风险: 止损 = entry ± entry_atr_mult * ATR (对称风险)
    entry_atr_mult = DecimalParameter(0.8, 2.0, default=1.5, space='sell', optimize=True)

    # TP1 利润目标 (百分比, 非 ATR)
    tp1_profit_pct = DecimalParameter(0.02, 0.10, default=0.05, space='sell', optimize=True)

    # 阶段2 追踪回撤百分比 (从最高点回落多少出场)
    trail_pct = DecimalParameter(0.01, 0.05, default=0.03, space='sell', optimize=True)

    # 最大持仓时间 (小时, 防止无限期持仓)
    max_hold_hours = IntParameter(4, 72, default=24, space='sell', optimize=True)

    # 仓位比例
    stake_single = DecimalParameter(0.15, 0.35, default=0.20, space='buy', optimize=False)
    stake_cont = DecimalParameter(0.20, 0.45, default=0.30, space='buy', optimize=False)

    # ── 指标计算 ───────────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD
        macd = ta.MACD(
            dataframe,
            fastperiod=self.MACD_FAST,
            slowperiod=self.MACD_SLOW,
            signalperiod=self.MACD_SIGNAL,
        )
        dataframe['macdhist'] = macd['macdhist']

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.ATR_PERIOD)

        # 深浅色柱子
        h = dataframe['macdhist']
        prev_h = h.shift(1)
        dataframe['hist_dark'] = ((h > 0) & (h > prev_h)) | ((h < 0) & (h < prev_h))
        dataframe['hist_light'] = ((h > 0) & (h < prev_h)) | ((h < 0) & (h > prev_h))
        dataframe['dark_to_light'] = dataframe['hist_dark'].shift(1) & dataframe['hist_light']

        # 背离检测
        dataframe = self._detect_divergence(dataframe, is_long=True)
        dataframe = self._detect_divergence(dataframe, is_long=False)

        # 预计算入场止损距离 (用于信息展示, 实际在 custom_stoploss 计算)
        dataframe['entry_sl_dist'] = dataframe['atr'] * self.entry_atr_mult.value

        return dataframe

    # ── 背离检测 (完全保留原有逻辑) ───────────────────────────────────────────

    @staticmethod
    def _calc_strength(curr_val: float, prev_val: float) -> float:
        if abs(prev_val) < 1e-10:
            return 0.0
        return abs(curr_val - prev_val) / abs(prev_val)

    def _find_key_candle(self, hist, dtl, extremum_idx, is_long):
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
        mask = (hist[all_extreme_idx] < 0) if is_long else (hist[all_extreme_idx] > 0)
        extreme_idx = all_extreme_idx[mask]

        sig_arr = np.zeros(n, dtype=bool)
        count_arr = np.zeros(n, dtype=np.int32)
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

            threshold = self.min_single_strength.value if consecutive == 1 else self.min_cont_strength.value
            if total_strength < threshold:
                continue

            sig_arr[key_idx] = True
            count_arr[key_idx] = consecutive
            strength_arr[key_idx] = total_strength

        prefix = 'bullish' if is_long else 'bearish'
        dataframe[f'{prefix}_signal'] = sig_arr
        dataframe[f'{prefix}_div_count'] = count_arr
        dataframe[f'{prefix}_div_strength'] = strength_arr

        return dataframe

    # ── 入场 / 出场信号 (完全保留) ─────────────────────────────────────────────

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
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    # ── 仓位管理 (完全保留) ────────────────────────────────────────────────────

    def custom_stake_amount(self, current_time, current_rate, proposed_stake, min_stake,
                           max_stake, leverage, entry_tag, side, **kwargs) -> float:
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

    # ── 自定义止损 (核心变更) ──────────────────────────────────────────────────

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit,
                       after_fill, **kwargs) -> float:
        """
        三阶段风险管理:
        1. 入场止损: 基于入场价的对称 ATR 风险
        2. TP1 触发后: 百分比追踪止盈
        3. 最大持仓时间: 强制退出
        """
        # 初始化缓存
        entry_rate = trade.get_custom_data(self._KEY_ENTRY_RATE)
        if entry_rate is None:
            entry_rate = trade.open_rate
            trade.set_custom_data(self._KEY_ENTRY_RATE, entry_rate)
            trade.set_custom_data(self._KEY_ENTRY_TIME, current_time)

        tp1_done = trade.get_custom_data(self._KEY_TP1_DONE) or False

        # 阶段1: 固定 ATR 止损 (从入场价计算, 对称)
        if not tp1_done:
            # 获取入场时 ATR
            atr_entry = self._get_atr_at_entry(pair, trade)
            if atr_entry is None:
                atr_entry = 0.0

            sl_dist = self.entry_atr_mult.value * atr_entry
            if trade.is_short:
                sl_price = entry_rate + sl_dist
                sl = stoploss_from_open(sl_dist / entry_rate, current_profit,
                                       is_short=True, leverage=trade.leverage)
            else:
                sl_price = entry_rate - sl_dist
                sl = stoploss_from_open(-sl_dist / entry_rate, current_profit,
                                       is_short=False, leverage=trade.leverage)

            # 硬上限
            return max(sl, -0.50)

        # 阶段2: 百分比追踪止盈
        trail_pct = self.trail_pct.value

        if trade.is_short:
            trail_min = trade.get_custom_data(self._KEY_TRAIL_MIN)
            if trail_min is None or current_rate < trail_min:
                trail_min = current_rate
                trade.set_custom_data(self._KEY_TRAIL_MIN, trail_min)
            # 价格反弹 trail_pct 时出场
            trigger_price = trail_min * (1 + trail_pct)
            sl = stoploss_from_open(
                (trigger_price - entry_rate) / entry_rate,
                current_profit, is_short=True, leverage=trade.leverage
            )
        else:
            trail_max = trade.get_custom_data(self._KEY_TRAIL_MAX)
            if trail_max is None or current_rate > trail_max:
                trail_max = current_rate
                trade.set_custom_data(self._KEY_TRAIL_MAX, trail_max)
            # 价格回落 trail_pct 时出场
            trigger_price = trail_max * (1 - trail_pct)
            sl = stoploss_from_open(
                (trigger_price - entry_rate) / entry_rate,
                current_profit, is_short=False, leverage=trade.leverage
            )

        # 保证至少保本
        breakeven = stoploss_from_open(0.0, current_profit,
                                      is_short=trade.is_short, leverage=trade.leverage)
        return max(sl, breakeven, -0.005)

    def _get_atr_at_entry(self, pair, trade):
        """获取入场时的 ATR 值"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None
        try:
            entry_ts = trade.open_date_utc
            df_before = dataframe[dataframe['date'] <= entry_ts]
            if df_before.empty:
                return None
            return float(df_before['atr'].iloc[-1])
        except Exception:
            return None

    # ── 自定义退出 (时间退出 + TP1 检查) ───────────────────────────────────────

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        """
        处理 TP1 利润目标和最大持仓时间退出
        """
        entry_time = trade.get_custom_data(self._KEY_ENTRY_TIME)
        if entry_time is None:
            entry_time = trade.open_date_utc
            trade.set_custom_data(self._KEY_ENTRY_TIME, entry_time)

        # 最大持仓时间检查
        hold_hours = (current_time - entry_time).total_seconds() / 3600
        max_hours = self.max_hold_hours.value

        if hold_hours >= max_hours:
            return f"time_exit_{int(hold_hours)}h"

        # TP1 利润目标检查 (50% 减仓)
        tp1_done = trade.get_custom_data(self._KEY_TP1_DONE) or False
        if tp1_done:
            return None

        tp1_target = self.tp1_profit_pct.value

        if current_profit >= tp1_target:
            # 标记 TP1 完成, 由 adjust_trade_position 执行减仓
            # 这里返回 None, 实际减仓在 adjust_trade_position
            pass

        return None

    # ── 分批止盈 (TP1 减仓) ────────────────────────────────────────────────────

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit,
                             min_stake, max_stake, current_entry_rate, current_exit_rate,
                             current_entry_profit, current_exit_profit, **kwargs):
        """TP1 减仓: 达到利润目标时减仓 50%"""
        if trade.get_custom_data(self._KEY_TP1_DONE):
            return None

        tp1_target = self.tp1_profit_pct.value

        if current_profit >= tp1_target:
            reduce_stake = -(trade.stake_amount * 0.5)
            if abs(reduce_stake) >= min_stake:
                trade.set_custom_data(self._KEY_TP1_DONE, True)
                # 初始化追踪基准
                trade.set_custom_data(self._KEY_TRAIL_MAX, current_rate)
                trade.set_custom_data(self._KEY_TRAIL_MIN, current_rate)
                return reduce_stake

        return None

    # ── 杠杆 ───────────────────────────────────────────────────────────────────

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                max_leverage, entry_tag, side, **kwargs) -> float:
        return 2.0
