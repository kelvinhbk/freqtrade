"""
MACD Histogram Divergence Strategy V4

核心逻辑:
  - 检测 MACD histogram 与价格的背离 (order=1 局部极值)
  - 底背离做多: 价格持续创新低, MACD 波谷持续抬高
  - 顶背离做空: 价格持续创新高, MACD 波峰持续降低
  - 关键K线: 背离后第一根满足 dark→light 转换的 K 线
      (多单要求 hist < 0, 空单要求 hist > 0)
  - 入场: 关键K线收盘时入场 (下一根 K 线开盘价)
  - 止损: 关键K线最低价 - ATR*1.5 (多单) / 最高价 + ATR*1.5 (空单)
  - TP1 (50% 仓位): open_rate + 2.25*ATR = open + 1.5*(1.5*ATR), 即 1:1.5 盈亏比
  - TP2 (50% 仓位): ATR 追踪止盈 (trail_max - N*current_ATR)
  - 无 DCA
  - 杠杆: 2x

与 macd_v2 的核心差异:
  1. MACD / ATR 参数固定 (不 hyperopt): 13/34/9, ATR=13
  2. 关键K线严格验证: dark_to_light=True AND hist < 0 (多) / hist > 0 (空)
  3. 阈值分离: 单次背离 0.372, 连续背离总强度 0.618
  4. TP1 基于 ATR (open + 2.25*ATR), 非风险距离倍数
  5. 右侧止盈改为 ATR 追踪 (替代利润回撤50%)
  6. 无 DCA 加仓逻辑
"""

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    stoploss_from_absolute,
    stoploss_from_open,
)
from pandas import DataFrame
import talib.abstract as ta
import numpy as np
from scipy.signal import argrelextrema


class macd_v4(IStrategy):

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = '15m'

    # ── 常量 ────────────────────────────────────────────────────────────────────

    _TAG_BULL_SINGLE = 'bull_single'
    _TAG_BULL_CONT   = 'bull_cont'
    _TAG_BEAR_SINGLE = 'bear_single'
    _TAG_BEAR_CONT   = 'bear_cont'

    _KEY_SL_PRICE  = 'sl_price'
    _KEY_ATR_ENTRY = 'atr_at_entry'
    _KEY_TP1_DONE  = 'tp1_done'
    _KEY_TRAIL_MAX = 'trail_max'
    _KEY_TRAIL_MIN = 'trail_min'

    # 兜底 ROI (实际通过 custom_stoploss / adjust_trade_position 出场)
    minimal_roi = {"0": 0.20}

    # 兜底止损 (会被 custom_stoploss 覆盖)
    stoploss = -0.15
    use_custom_stoploss = True
    trailing_stop = False
    position_adjustment_enable = True  # 启用 adjust_trade_position (TP1 减仓必须)

    # 启动需要足够历史数据计算 MACD (slow=34) + ATR (13)
    startup_candle_count = 100

    # ── 固定规格参数 (不 hyperopt, 规格文档明确指定) ──────────────────────────

    MACD_FAST: int = 13
    MACD_SLOW: int = 34
    MACD_SIGNAL: int = 9
    ATR_PERIOD: int = 13

    # ── Hyperopt 参数 ──────────────────────────────────────────────────────────

    # 单次背离强度阈值 (规格: 0.372 = 黄金比例补数)
    # 公式: (curr_val - prev_val) / |prev_val| > min_single_strength
    min_single_strength = DecimalParameter(0.20, 0.60, default=0.597, space='buy', optimize=True)

    # 连续背离总强度阈值 (规格: 0.618 = 黄金比例)
    # 公式: sum((curr_i - prev_i) / |prev_i|) > min_cont_strength
    min_cont_strength = DecimalParameter(0.40, 0.90, default=0.853, space='buy', optimize=True)

    # TP1: open_rate + tp1_atr_mult * atr_at_entry
    # 规格: 1.5 * (1.5 * ATR) = 2.25 * ATR → default=2.25
    tp1_atr_mult = DecimalParameter(1.5, 3.5, default=1.597, space='sell', optimize=True)

    # ATR 止损倍数: 关键K线 low/high ± atr_sl_mult * ATR
    atr_sl_mult = DecimalParameter(1.0, 2.5, default=2.163, space='sell', optimize=True)

    # ATR 追踪止盈倍数 (trail_max - trail_atr_mult * current_ATR)
    trail_atr_mult = DecimalParameter(1.5, 3.5, default=1.591, space='sell', optimize=True)

    # 仓位比例 (不 hyperopt, 直接控制)
    stake_single = DecimalParameter(0.15, 0.35, default=0.20, space='buy', optimize=False)
    stake_cont = DecimalParameter(0.20, 0.45, default=0.30, space='buy', optimize=False)

    # ── 指标计算 ───────────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD (只使用 histogram, 不显示 MACD 线和信号线)
        macd = ta.MACD(
            dataframe,
            fastperiod=self.MACD_FAST,
            slowperiod=self.MACD_SLOW,
            signalperiod=self.MACD_SIGNAL,
        )
        dataframe['macdhist'] = macd['macdhist']

        # ATR (用于止损/止盈计算)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.ATR_PERIOD)

        # 深浅色柱子识别
        # 深色 (hist 绝对值增大, 动能增强):
        #   绿区增强: hist > 0 且 > 前一根
        #   红区增强: hist < 0 且 < 前一根 (负数更小 = 绝对值更大)
        # 浅色 (hist 绝对值缩小, 动能减弱):
        #   绿区减弱: hist > 0 且 < 前一根
        #   红区减弱: hist < 0 且 > 前一根 (负数更大 = 绝对值更小)
        h = dataframe['macdhist']
        prev_h = h.shift(1)
        dataframe['hist_dark'] = ((h > 0) & (h > prev_h)) | ((h < 0) & (h < prev_h))
        dataframe['hist_light'] = ((h > 0) & (h < prev_h)) | ((h < 0) & (h > prev_h))

        # 深转浅: 前一根深色, 当前浅色 → 关键K线候选
        dataframe['dark_to_light'] = dataframe['hist_dark'].shift(1) & dataframe['hist_light']

        # 背离检测 + 关键K线标记
        dataframe = self._detect_divergence(dataframe, is_long=True)
        dataframe = self._detect_divergence(dataframe, is_long=False)

        # 在关键K线位置预计算止损价
        dataframe['sl_price_long'] = np.where(
            dataframe['bullish_signal'],
            dataframe['low'] - dataframe['atr'] * self.atr_sl_mult.value,
            np.nan,
        )
        dataframe['sl_price_short'] = np.where(
            dataframe['bearish_signal'],
            dataframe['high'] + dataframe['atr'] * self.atr_sl_mult.value,
            np.nan,
        )

        return dataframe

    # ── 背离检测辅助 ───────────────────────────────────────────────────────────

    @staticmethod
    def _calc_strength(curr_val: float, prev_val: float) -> float:
        """
        背离强度 = |curr - prev| / |prev| (无量纲)

        底背离 (curr > prev, 两者均为负):
          curr=-0.6, prev=-1.0 → strength = |−0.6 − (−1.0)| / 1.0 = 0.4

        顶背离 (curr < prev, 两者均为正):
          curr=0.7, prev=1.0 → strength = |0.7 − 1.0| / 1.0 = 0.3
        """
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
        """
        从 extremum_idx 之后搜索关键K线 (最多 4 根)。

        关键K线条件 (v4 严格版):
          1. dark_to_light = True (深转浅的第一根)
          2. hist 在正确区域: 多单要求 hist < 0, 空单要求 hist > 0

        返回 key_idx 或 None (找不到则跳过此次背离)
        """
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
        """
        背离检测, 标记关键K线。

        is_long=True  底背离 (做多): 负区波谷抬高 + 价格创新低
        is_long=False 顶背离 (做空): 正区波峰降低 + 价格创新高

        算法:
        1. 找对应区域 (hist < 0 / hist > 0) 的局部极值 (order=1)
        2. 对每个极值点, 在前 50 根内找历史极值列表
        3. 从最近历史极值向前统计连续背离链, 链断即停
        4. 关键K线: 极值点之后第一根满足 dark_to_light AND 在正确区域 (最多4根)
        5. 强度过滤: 单次 > min_single_strength, 连续 > min_cont_strength

        输出列 (prefix = bullish / bearish):
          {prefix}_signal       : bool
          {prefix}_div_count    : int
          {prefix}_div_strength : float
        """
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
                # 底背离: hist 抬高 (负数更小) + 价格创新低
                # 顶背离: hist 降低 (正数更小) + 价格创新高
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

        # 做多: 单次底背离 (强度 > 0.372, 仓位 20%)
        dataframe.loc[
            dataframe['bullish_signal'] & (dataframe['bullish_div_count'] == 1) & vol,
            ['enter_long', 'enter_tag'],
        ] = (1, self._TAG_BULL_SINGLE)

        # 做多: 连续底背离 (总强度 > 0.618, 仓位 30%)
        dataframe.loc[
            dataframe['bullish_signal'] & (dataframe['bullish_div_count'] >= 2) & vol,
            ['enter_long', 'enter_tag'],
        ] = (1, self._TAG_BULL_CONT)

        # 做空: 单次顶背离 (强度 > 0.372, 仓位 20%)
        dataframe.loc[
            dataframe['bearish_signal'] & (dataframe['bearish_div_count'] == 1) & vol,
            ['enter_short', 'enter_tag'],
        ] = (1, self._TAG_BEAR_SINGLE)

        # 做空: 连续顶背离 (总强度 > 0.618, 仓位 30%)
        dataframe.loc[
            dataframe['bearish_signal'] & (dataframe['bearish_div_count'] >= 2) & vol,
            ['enter_short', 'enter_tag'],
        ] = (1, self._TAG_BEAR_CONT)

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 出场完全由 custom_stoploss / adjust_trade_position 控制
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
        """
        根据背离类型动态调整仓位:
          单次背离 (bull_single / bear_single): 20% 可用资金
          连续背离 (bull_cont  / bear_cont  ): 30% 可用资金
        """
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

    # ── 自定义止损 ─────────────────────────────────────────────────────────────

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
        两阶段止损:

        阶段1 (TP1 触发前): 固定 ATR 止损
          多单止损 = 关键K线最低价 - ATR * 1.5
          空单止损 = 关键K线最高价 + ATR * 1.5

        阶段2 (TP1 触发后): ATR 追踪止盈
          多单: 追踪价格最高点, 当价格低于 trail_max - trail_atr_mult * current_ATR 时出场
          空单: 追踪价格最低点, 当价格高于 trail_min + trail_atr_mult * current_ATR 时出场
          最低保证: 不低于成本价 (保本)
        """
        # 首次调用: 从 dataframe 读取入场时的止损价和 ATR 值并缓存
        sl_price: float | None = trade.get_custom_data(self._KEY_SL_PRICE)
        if sl_price is None:
            sl_price, atr_at_entry = self._load_entry_data(pair, trade)
            if sl_price is None:
                return self.stoploss
            trade.set_custom_data(self._KEY_SL_PRICE, sl_price)
            trade.set_custom_data(self._KEY_ATR_ENTRY, atr_at_entry)

        tp1_done: bool = trade.get_custom_data(self._KEY_TP1_DONE) or False

        if not tp1_done:
            # 阶段1: 固定止损
            sl = stoploss_from_absolute(
                sl_price, current_rate, is_short=trade.is_short, leverage=trade.leverage
            )
            return max(sl, self.stoploss)

        # 阶段2: ATR 追踪止盈
        return self._calc_trailing_stoploss(pair, trade, current_rate, current_profit)

    def _load_entry_data(self, pair: str, trade) -> tuple[float | None, float | None]:
        """
        从 dataframe 中找到入场时最近关键K线的止损价和 ATR 值。
        只在 custom_stoploss 首次调用时执行一次, 结果缓存到 trade custom data。
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None, None

        sl_col = 'sl_price_short' if trade.is_short else 'sl_price_long'
        sig_col = 'bearish_signal' if trade.is_short else 'bullish_signal'

        try:
            entry_ts = trade.open_date_utc
            df_before = dataframe[dataframe['date'] <= entry_ts]
            recent_signals = df_before[df_before[sig_col] == True]
            if recent_signals.empty:
                return None, None
            last_signal = recent_signals.tail(1).iloc[0]
            sl_price = float(last_signal[sl_col])
            atr_val = float(last_signal['atr'])
            if np.isnan(sl_price) or np.isnan(atr_val):
                return None, None
            return sl_price, atr_val
        except Exception:
            return None, None

    def _calc_trailing_stoploss(
        self,
        pair: str,
        trade,
        current_rate: float,
        current_profit: float,
    ) -> float:
        """
        ATR 追踪止盈 (阶段2, TP1 触发后)。

        多单: 追踪 trail_max, 止盈触发价 = trail_max - trail_atr_mult * current_ATR
        空单: 追踪 trail_min, 止盈触发价 = trail_min + trail_atr_mult * current_ATR

        使用当前 ATR (自适应波动), 同时保证不低于保本价。
        """
        # 获取当前 ATR (优先用实时值, 回退到入场时缓存值)
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if not dataframe.empty:
            current_atr = float(dataframe.iloc[-1]['atr'])
        else:
            current_atr = trade.get_custom_data(self._KEY_ATR_ENTRY) or 0.0

        trail_mult = self.trail_atr_mult.value

        if trade.is_short:
            trail_min = trade.get_custom_data(self._KEY_TRAIL_MIN)
            if trail_min is None or current_rate < trail_min:
                trail_min = current_rate
                trade.set_custom_data(self._KEY_TRAIL_MIN, trail_min)
            trail_sl_price = trail_min + trail_mult * current_atr
            sl = stoploss_from_absolute(
                trail_sl_price, current_rate, is_short=True, leverage=trade.leverage
            )
        else:
            trail_max = trade.get_custom_data(self._KEY_TRAIL_MAX)
            if trail_max is None or current_rate > trail_max:
                trail_max = current_rate
                trade.set_custom_data(self._KEY_TRAIL_MAX, trail_max)
            trail_sl_price = trail_max - trail_mult * current_atr
            sl = stoploss_from_absolute(
                trail_sl_price, current_rate, is_short=False, leverage=trade.leverage
            )

        # 保证至少保本
        breakeven_sl = stoploss_from_open(
            0.0, current_profit, is_short=trade.is_short, leverage=trade.leverage
        )
        return max(sl, breakeven_sl, -0.005)

    # ── 分批止盈 (TP1) ─────────────────────────────────────────────────────────

    def adjust_trade_position(
        self,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        min_stake: float,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        """
        TP1 减仓: 第一批 50% 仓位在 1:1.5 盈亏比时止盈。

        触发价计算:
          多单 TP1 = open_rate + tp1_atr_mult * atr_at_entry = open + 2.25 * ATR
          空单 TP1 = open_rate - tp1_atr_mult * atr_at_entry = open - 2.25 * ATR

        其中 2.25 = 1.5 (盈亏比) × 1.5 (ATR 止损倍数)
        触发后:
          - 减仓 50%
          - 设 tp1_done=True, 止盈转由 custom_stoploss 阶段2 (ATR追踪) 接管
          - 初始化追踪基准价 (trail_max / trail_min)
        """
        if trade.get_custom_data(self._KEY_TP1_DONE):
            return None

        atr_at_entry: float | None = trade.get_custom_data(self._KEY_ATR_ENTRY)
        if atr_at_entry is None:
            return None

        tp1_dist = self.tp1_atr_mult.value * atr_at_entry

        if trade.is_short:
            reached = current_rate <= trade.open_rate - tp1_dist
        else:
            reached = current_rate >= trade.open_rate + tp1_dist

        if reached:
            reduce_stake = -(trade.stake_amount * 0.5)
            if abs(reduce_stake) >= min_stake:
                trade.set_custom_data(self._KEY_TP1_DONE, True)
                # 初始化追踪基准价
                trade.set_custom_data(self._KEY_TRAIL_MAX, current_rate)
                trade.set_custom_data(self._KEY_TRAIL_MIN, current_rate)
                return reduce_stake

        return None

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
