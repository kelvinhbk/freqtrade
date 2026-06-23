# EfutureLong_v4 风控优化设计

> 基于 EfutureLong.py 创建新版本 EfutureLong_v4.py，重点优化止盈止损机制。

## 背景

EfutureLong 策略实盘运行中暴露两个核心问题：
1. 开仓后前 10 分钟内利润冲到 8%+ 时缺乏有效止盈，利润大幅回撤
2. 最大单笔亏损达 31%（3x 杠杆下 stoploss=-0.10 意味着价格跌 10% 才触发）

根因分析：
- `stoploss = -0.10` + 3x 杠杆 = 价格跌 10% 触发 = 实际亏损 30%
- `custom_stoploss` 的三阶段止损没有硬上限约束
- 无利润依赖的 trailing 机制，大利润时止损线不够紧

## 设计方案：三重保护层（混合方案）

### 第一层：custom_stoploss — trailing 止盈 + 动态止损

所有 trailing 和止损逻辑统一在 `custom_stoploss` 中处理。

#### 利润依赖的 trailing 止损

利润越高 -> trailing 距离越小 -> 止损线越紧：

| 当前利润 | trailing 距离（相对 current_rate） | 效果 |
|-----------|-----------------------------------|------|
| > tier3_profit (默认 15%) | -tier3_distance (默认 -0.01) | 仅允许 1% 回撤 |
| > tier2_profit (默认 8%) | -tier2_distance (默认 -0.02) | 允许 2% 回撤 |
| > tier1_profit (默认 3%) | -tier1_distance (默认 -0.03) | 允许 3% 回撤 |
| < tier1_profit | 时间 + ATR 动态止损 | 正常止损 |

#### 时间 + ATR 动态止损（无利润时）

```
elapsed < 30min:  max(-0.05, csl_initial)     # 初期紧缩
30min ~ 4h:      max(-0.05, atr_pct)          # ATR 动态但不超过 -5%
> 4h:            max(csl_late, atr_pct)       # 后期收窄
```

`self.stoploss = -0.05` 作为最后防线（价格跌 5% = 实际亏损 15%）。

#### 伪代码

```python
def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
    # 优先级最高：利润依赖的 trailing
    if current_profit > self.trail_tier3_profit.value:
        return -self.trail_tier3_distance.value
    if current_profit > self.trail_tier2_profit.value:
        return -self.trail_tier2_distance.value
    if current_profit > self.trail_tier1_profit.value:
        return -self.trail_tier1_distance.value

    # 时间 + ATR 动态止损
    dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
    if len(dataframe) < 1:
        return self.stoploss

    current_candle = dataframe.iloc[-1]
    atr = current_candle["atr"]
    elapsed = (current_time - trade.open_date_utc).total_seconds() / 60
    atr_pct = max(-(atr / current_rate) * self.csl_mid_ratio.value, self.stoploss)

    if elapsed < 30:
        return self.csl_initial  # -0.05
    elif elapsed < 240:
        return max(-0.05, atr_pct)
    else:
        return max(self.csl_late.value, atr_pct)
```

### 第二层：minimal_roi — 兜底止盈

作为安全网，只在极端情况下（瞬间暴拉后急跌，trailing 来不及收紧）触发。

```python
minimal_roi = {
    "0":   0.25,   # 价格涨 ~8.3% 时止盈（实际利润 ~25%）
    "10":  0.15,   # 10 分钟后阈值降至 15%
    "30":  0.10,   # 30 分钟后降至 10%
    "60":  0.07,   # 1 小时后降至 7%
    "120": 0.04,   # 2 小时后降至 4%
    "240": 0.02,   # 4 小时后降至 2%
    "480": 0.01,   # 8 小时后降至 1%
}
```

注意：freqtrade 执行顺序为 `stoploss -> ROI -> custom_exit`。正常情况下 trailing 会在 ROI 触发前锁定利润，ROI 只做兜底。

### 第三层：custom_exit — 信号类退出

保留并优化现有的信号退出逻辑：

| 层级 | 逻辑 | 变更 |
|------|------|------|
| L1 趋势退出 | weak_trend + low_volatility + ema50_uptrend | 保留 |
| L2 利润退出 | fastk > threshold | **移除**（被 trailing 替代） |
| L2 MACD 反转 | profit > threshold AND macd_cross_down | 保留 |
| L3 亏损缓解 | CCI > 80 + bearish | 保留 |
| L4 时间止损 | 7h / 10h 分档 | **重新设计为 4h / 7h / 10h 三档** |

时间退出新档位：

| 档位 | 小时 | 阈值 | 说明 |
|------|------|------|------|
| 1 | 4 (可优化 3~6) | -0.02 (可优化 -0.05~-0.01) | 早期亏损截断 |
| 2 | 7 (可优化 6~9) | -0.01 (可优化 -0.03~-0.005) | 中期收紧 |
| 3 | 10 (可优化 9~14) | 无条件退出 | 强制离场 |

## 保护机制增强

从单一冷却期扩展为三层保护：

```python
protections = [
    {"method": "CooldownPeriod", "stop_duration_candles": 48},
    {"method": "StoplossGuard", "lookback_period_candles": 60,
     "trade_limit": 2, "stop_duration_candles": 40},
    {"method": "MaxDrawdown", "trade_limit": 20,
     "stop_duration_candles": 80, "max_allowed_drawdown": -0.15},
]
```

保护参数用固定值，不参与 hyperopt（影响资金安全，不适合用历史收益优化）。

## 不改动的部分

- 入场信号（mean-reversion + trend-following）— 已经过多轮优化
- 杠杆固定 3x — 已验证
- `populate_indicators` 的指标计算 — 与入场信号耦合
- `startup_candle_count = 310` — EMA200 需要足够历史数据

## 参数变更汇总

### 移除的参数

- `sell_fastx` — fastk 利润退出被 trailing 替代

### 修改的参数

| 参数 | 旧值 | 新值 |
|------|------|------|
| `stoploss` | -0.10 | -0.05 |
| `csl_initial` | -0.10 | -0.05 |
| `minimal_roi` | 4 档 | 7 档 |

### 新增参数（全部可 hyperopt）

| 参数 | 默认值 | 范围 | 空间 |
|------|--------|------|------|
| `trail_tier1_profit` | 0.03 | 0.02~0.08 | sell |
| `trail_tier1_distance` | 0.03 | 0.01~0.04 | sell |
| `trail_tier2_profit` | 0.08 | 0.06~0.15 | sell |
| `trail_tier2_distance` | 0.02 | 0.005~0.03 | sell |
| `trail_tier3_profit` | 0.15 | 0.10~0.25 | sell |
| `trail_tier3_distance` | 0.01 | 0.003~0.02 | sell |
| `time_exit_1_hours` | 4 | 3~6 | sell |
| `time_exit_1_threshold` | -0.02 | -0.05~-0.01 | sell |
| `time_exit_2_hours` | 7 | 6~9 | sell |
| `time_exit_2_threshold` | -0.01 | -0.03~-0.005 | sell |
| `time_exit_3_hours` | 10 | 9~14 | sell |

约束条件：`trail_tier1_profit < trail_tier2_profit < trail_tier3_profit`，代码中需保证。

### 保留不动的参数（21 个）

所有 `buy_*` 参数（11 个）、`sell_trend_filter`、`sell_macd_profit`、`csl_mid_ratio`、`csl_late`、`exit_adx_filter`、`exit_volatility_filter`。

## 预期效果

| 指标 | EfutureLong | EfutureLong_v4 |
|------|-------------|----------------|
| 最大单笔亏损 | ~31% | <= 15% |
| 8%+ 利润锁定率 | 低 | 高（trailing 自动跟随） |
| 最优利润回撤 | 可能从 +15% 回到 -5% | 从 +15% 最多回到 +5% |
| 可优化参数数 | 21 | 32 |

## 实现步骤

1. 复制 EfutureLong.py 为 EfutureLong_v4.py，重命名类名
2. 修改 `stoploss`、`csl_initial`、`minimal_roi`
3. 新增 trailing 参数定义
4. 重写 `custom_stoploss` 实现 trailing 逻辑
5. 简化 `custom_exit`：移除 fastk 退出，重设时间退出档位
6. 更新 `protections`
7. 回测验证
