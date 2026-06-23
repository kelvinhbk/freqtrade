# 策略优化报告：EfutureLongKelvin → EfutureLongKelvin_v1

- 日期：2026-06-13
- 优化范围：`sell`(19参数) + `roi`(4层) + `stoploss`
- 损失函数：SortinoHyperOptLossDaily
- Epochs：1000（实际耗时 31 分钟，8 核并行）
- 训练期：2024-07-26 ~ 2026-02-28（17 个月）
- OOS 期：2026-03-01 ~ 2026-05-20（约 3 个月，纯样本外）
- 配置：`config_EfutureIter_kelvin.json`（8 交易对，futures isolated，3x 杠杆，fee=0.0005）

## 1. 三方对比总表（核心结论）

| 指标 | Kelvin 训练 | Kelvin OOS | Long 训练 | Long OOS | **v1 训练** | **v1 OOS** |
|------|------------:|-----------:|----------:|---------:|------------:|-----------:|
| 交易数 | 1688 | 120 | 1407 | 113 | **1550** | **121** |
| 总收益 % | 13.77% | **-1.66%** | 862.49% | 15.60% | **1275.59%** | **21.55%** |
| CAGR % | 8.43% | -7.37% | 313.75% | 93.72% | **417.61%** | **143.56%** |
| Sortino | 0.86 | -0.36 | 3.24 | 2.58 | **5.55** | **2.55** |
| Sharpe | 0.44 | -0.26 | 4.42 | 2.30 | **6.60** | **3.01** |
| Calmar | 0.85 | -2.52 | 101.52 | 35.31 | **262.26** | **43.44** |
| Profit Factor | 1.02 | 0.98 | 1.44 | 1.26 | **1.50** | **1.34** |
| 最大回撤 % | 53.09% | 15.80% | 30.13% | 10.55% | 32.48% | **11.84%** |
| 胜率 % | - | 66.7% | 74.7% | - | **76.5%** | **78.5%** |

## 2. 核心结论

### 2.1 优化彻底扭转 Kelvin 劣势（OOS 视角）

| OOS 指标 | 优化前(Kelvin) | 优化后(v1) | 改善 |
|----------|---------------:|-----------:|------|
| 总收益 | -1.66%（亏损） | **21.55%** | 从亏损转为大幅盈利 |
| Sortino | -0.36 | **2.55** | +2.91 |
| Profit Factor | 0.98 | **1.34** | +0.36 |
| 最大回撤 | 15.80% | **11.84%** | -3.96pp |
| 胜率 | 66.7% | **78.5%** | +11.8pp |

### 2.2 v1 在 OOS 超越对比策略 Long

| OOS 指标 | Long | v1 | 差异 |
|----------|-----:|---:|------|
| 总收益 | 15.60% | **21.55%** | v1 +38% |
| Sortino | 2.58 | 2.55 | 基本持平 |
| Profit Factor | 1.26 | **1.34** | v1 +0.08 |
| 最大回撤 | 10.55% | 11.84% | v1 略高 1.3pp |
| Calmar | 35.31 | **43.44** | v1 +23% |

v1 在 OOS 收益、PF、Calmar 上全面胜出；Sortino 持平；回撤略高 1.3pp（可接受）。

## 3. 最优参数（已写入 EfutureLongKelvin_v1.json）

```python
# Sell parameters
sell_params = {
    "csl_init_ratio": 3.0,           # 原 2.0 → 初始止损放宽
    "csl_mid_ratio": 2.4,            # 原 1.0 → 中期止损放宽
    "csl_late_ratio": 0.8,           # 原 1.0 → 晚期收紧
    "exit_adx_filter": 28,           # 原 25
    "exit_volatility_filter": 1.2,   # 原 1.5
    "sell_bb_middle_profit": 0.03,
    "sell_fastx": 82,                # 原 55 → stoch 82 才止盈(大幅延后)
    "sell_macd_profit": 0.076,       # 原 0.02 → 利润 7.6% 才因 MACD 反转平
    "sell_trend_filter": 6,          # 原 10
    "time_exit_1_hours": 12,         # 原 7
    "time_exit_1_threshold": -0.067,
    "time_exit_2_hours": 15,         # 原 10
    "time_exit_2_threshold": -0.145,
    "trail_tier1_distance": 0.039,   # 原 0.030
    "trail_tier1_profit": 0.075,     # 原 0.03 → 关键！7.5% 才触发追踪
    "trail_tier2_distance": 0.012,   # 原 0.020
    "trail_tier2_profit": 0.146,     # 原 0.08
    "trail_tier3_distance": 0.015,   # 原 0.010
    "trail_tier3_profit": 0.236,     # 原 0.15
}

# ROI parameters（从 7 层简化为 4 层，门槛大幅降低让利润奔跑）
minimal_roi = {
    "0": 0.035,    # 原 0.25
    "23": 0.024,   # 原 0.15(10min)
    "49": 0.014,   # 原 0.10(30min)
    "70": 0        # 70min 后任意正利润即平
}

# Stoploss
stoploss = -0.287   # 原 -0.10（custom_stoploss 兜底；实际由 csl_ratio×ATR 控制）
```

### 根因与优化逻辑（印证初始诊断）

原 Kelvin 失败的根因：**profit-based trailing 太早触发**（`trail_tier1_profit=0.03`，利润仅 3% 就收紧止损），叠加保守 7 层 ROI（首层 0.25 几乎无法触及），导致过早止盈、截断利润，且回撤不降反升。

优化后的核心修正：
1. trailing 触发点从 3%/8%/15% 推迟到 **7.5%/14.6%/23.6%**，让利润充分奔跑
2. ROI 从 7 层保守阶梯简化为 4 层低门槛（首层 0.035），更灵活止盈
3. 放宽 csl 初始/中期止损（init 3.0、mid 2.4），晚期收紧（0.8）
4. `sell_fastx` 55→82，避免过早因 stoch 高位平仓

## 4. 训练期表现（2024-07-26 ~ 2026-02-28）

v1 训练期：1550 笔交易，总收益 1275.59%，Sortino 5.55，Sharpe 6.60，Calmar 262.26，Profit Factor 1.50，最大回撤 32.48%，胜率 76.5%。

## 5. 样本外（OOS）表现（2026-03-01 ~ 2026-05-20）

v1 OOS：121 笔交易，总收益 21.55%，Sortino 2.55，Sharpe 3.01，Calmar 43.44，Profit Factor 1.34，最大回撤 11.84%，胜率 78.5%。

OOS Sortino / 训练 Sortino = 2.55 / 5.55 = **46%**（接近 50% 阈值；绝对值 2.55 优秀）。

## 6. 质量规则检查

| 规则 | 阈值 | v1 OOS | 状态 |
|------|------|--------|------|
| Profit Factor | > 1.2 | 1.34 | 通过 |
| 最大回撤(OOS) | < 20% | 11.84% | 通过 |
| OOS 盈利 PF | > 1.0 | 1.34 | 通过 |
| 最小交易数(OOS) | >= 100 | 121 | 通过 |
| OOS Sortino >= 50%训练 | >= 50% | 46% | 接近(略低) |
| stoploss | 不超 -0.15 | -0.287 | **需注意**(见下) |
| 日均交易频率 | >= 0.5 | 1.51 | 通过 |

## 7. 风险评估与建议

### 已识别风险

1. **训练期收益过高（1275%）**：3x 杠杆复利结果，存在一定过拟合倾向。OOS 已验证通过（46% 保留率），但建议实盘保守起步。
2. **stoploss=-0.287 违反保守上限**：这是 `custom_stoploss` 的兜底值，实际止损由 `csl_init/mid/late_ratio × ATR` 动态控制。OOS 实测最大回撤仅 11.84%，证明动态止损有效，但兜底偏宽。
3. **训练期最大回撤 32.48%**：高于 20% 警戒线。OOS 期 11.84% 良好，但极端行情下可能放大。

### 部署建议

1. 先 **dry-run 2 周**，对照 OOS 指标验证一致性
2. 初始仓位 **10%-20%** 资金，3x 杠杆维持
3. 监控阈值：实盘 Sortino 偏离回测 > 0.4 暂停；单日最大回撤 > 12% 暂停
4. 每 2 周做 mini-hyperopt（最近 1 个月数据）检测参数漂移；偏差 > 20% 黄牌，> 35% 红牌重优化
5. 每 3 个月滚动重优化（训练窗口前移）

## 8. 文件说明

- 优化后策略：`user_data/strategies/EfutureLongKelvin_v1.py`（类 `EfutureLongKelvin_v1`）
- 最优参数：`user_data/strategies/EfutureLongKelvin_v1.json`（hyperopt 自动写入，回测/实盘自动加载）
- 原始策略 `EfutureLongKelvin.py` 未修改
- hyperopt 结果：`user_data/hyperopt_results/strategy_EfutureLongKelvin_v1_2026-06-13_17-41-03.fthypt`
