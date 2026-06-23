# EfutureLong 策略提取设计

## 背景

从双向策略 `AutoResearch_iter0002_E0010_E0014_E0033` 中提取做多部分，生成一个纯做多策略 `EfutureLong`，默认参数使用 hyperopt 最优结果。

## 目标

- 创建一个专注做多的策略，移除所有做空逻辑
- 使用最优参数作为默认值，开箱即用
- 代码干净、可维护

## 设计细节

### 1. 类定义与基础配置

- 类名：`EfutureLong`
- `can_short = False`
- `timeframe`、`process_only_new_candles`、`startup_candle_count`、`order_types`、`stoploss`、`trailing_stop`、`use_custom_stoploss`、`protections`、`leverage` 均保留原值
- `minimal_roi` 使用 JSON 中的最优 ROI 配置：
  ```python
  minimal_roi = {
      "0": 0.137,
      "20": 0.097,
      "57": 0.034,
      "169": 0,
  }
  ```

### 2. 参数清理

**保留的做多参数：**

| 参数 | 类型 | 范围 | 最优默认值 |
|------|------|------|-----------|
| buy_rsi_fast | IntParameter | 20-70 | 40 |
| buy_rsi | IntParameter | 15-50 | 49 |
| buy_sma15_ratio | DecimalParameter | 0.90-1.0 | 0.98 |
| buy_cti | DecimalParameter | -1-1 | -0.34 |
| buy_24h_min_pct | DecimalParameter | -30.0-0.0 | -19.2 |
| buy_24h_max_pct | DecimalParameter | 0.0-200.0 | 182.2 |
| buy_volume_sma | DecimalParameter | 0.8-1.5 | 1.09 |
| buy_adx | IntParameter | 15-40 | 23 |
| buy_tf_adx | IntParameter | 15-45 | 37 |
| buy_tf_rsi_min | IntParameter | 30-55 | 44 |
| buy_tf_rsi_max | IntParameter | 55-80 | 56 |
| buy_atr_ratio | DecimalParameter | 0.005-0.03 | 0.028 |
| buy_atr_sma_period | IntParameter | 10-50 | 33 |
| buy_trend_strength | IntParameter | 20-40 | 28 |

**保留的出场参数：**

| 参数 | 类型 | 范围 | 最优默认值 |
|------|------|------|-----------|
| sell_fastx | IntParameter | 50-100 | 55 |
| sell_trend_filter | IntParameter | 5-20 | 10 |
| sell_macd_profit | DecimalParameter | 0.005-0.10 | 0.02 |
| sell_bb_middle_profit | DecimalParameter | 0.005-0.05 | 0.015 |
| time_exit_1_hours | IntParameter | 4-12 | 7 |
| time_exit_1_threshold | DecimalParameter | -0.08--0.02 | -0.05 |
| time_exit_2_hours | IntParameter | 8-16 | 10 |
| time_exit_2_threshold | DecimalParameter | -0.15--0.05 | -0.10 |
| csl_mid_ratio | DecimalParameter | 1.0-3.0 | 1.0 |
| csl_late | DecimalParameter | -0.08--0.01 | -0.058 |
| exit_adx_filter | IntParameter | 15-35 | 25 |
| exit_volatility_filter | DecimalParameter | 0.8-2.0 | 1.5 |

**删除所有以 `short_` 开头的参数。**

### 3. 指标计算清理

保留共享指标：SMA、RSI、MACD、ADX、ATR、EMA、CCI、fastk、cti、volume_sma、24h_change_pct。

删除仅用于做空的指标：
- `strong_downtrend`

### 4. 入场逻辑

仅保留以下做多入场信号：

**long_mr**（均值回归做多）：
```python
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
```

**long_tf**（趋势跟踪做多）：
```python
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
```

删除所有 `short_mr` 和 `short_tf` 逻辑。

### 5. 出场与止损逻辑

**custom_stoploss**：
删除 `if trade.is_short` 分支，仅保留做多逻辑：
```python
if elapsed < 30:
    desired_pct = self.csl_initial
elif elapsed < 240:
    desired_pct = atr_pct
else:
    desired_pct = max(self.csl_late.value, atr_pct)

desired_stop_price = trade.open_rate * (1 + desired_pct)
return leverage_val * (desired_stop_price / current_rate - 1)
```

**custom_exit**：
删除所有 `trade.is_short` 分支，仅保留做多出场逻辑：
- fastk 盈利出场：`fastk > sell_fastx`
- MACD 反转出场：`macd_cross_down` 且盈利超过 `sell_macd_profit`
- CCI 止损：`cci > 80` 且 bearish
- 时间止损：保留两层时间止损逻辑

**populate_exit_trend**：保留原样（清空出场列）。

### 6. 输出文件

- 策略文件：`user_data/strategies/EfutureLong.py`

## 验证清单

- [ ] 策略仅包含做多参数（无 short_ 前缀参数）
- [ ] `can_short = False`
- [ ] 所有默认参数值来自 JSON 最优结果
- [ ] `custom_stoploss` 中无做空分支
- [ ] `custom_exit` 中无做空分支
- [ ] `populate_entry_trend` 中无做空入场逻辑
- [ ] 代码通过 ruff 检查
