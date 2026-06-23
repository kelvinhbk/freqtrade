# v1 vs v2 稳健性对比报告：OOS Sortino 保留率调优实验

- 日期：2026-06-13
- 目标：通过复合 loss 调优提高 v1 的 OOS Sortino 保留率（v1 为 46%，低于 50% 阈值）
- v1：纯 Sortino loss（SortinoHyperOptLossDaily），已固化最优参数
- v2：复合 loss（CompositeRobustLoss，Sortino+Calmar+PF+胜率+连续亏损+回撤惩罚）

## 1. 核心结论（先行）

**v2 复合 loss 调优未达成目标，反而显著劣化。v1 在 OOS 全面碾压 v2，应继续使用 v1。**

- v2 OOS Sortino 保留率 **19%**（v1 是 46%）—— 不升反降
- v2 OOS 收益 4.16%（v1 是 21.55%）
- v2 OOS 回撤 20.92%（v1 是 11.84%，v2 更不稳）

## 2. 完整对比表

| 指标 | v1 训练 | v1 OOS | v2 训练 | v2 OOS |
|------|--------:|-------:|--------:|-------:|
| 交易数 | 1550 | 121 | 1628 | 126 |
| 总收益 % | 1275.59% | **21.55%** | 113.08% | **4.16%** |
| Sortino | 5.55 | **2.55** | 3.77 | **0.71** |
| Sharpe | 6.60 | 3.01 | 2.74 | 0.69 |
| Calmar | 262.26 | 43.44 | 8.22 | 4.75 |
| Profit Factor | 1.50 | **1.34** | 1.14 | **1.07** |
| 最大回撤 % | 32.48% | **11.84%** | 45.15% | **20.92%** |
| 胜率 % | 76.5% | **78.5%** | 60.0% | **55.6%** |
| **OOS/训练保留率** | - | **46%** | - | **19%** |

## 3. v2 失败根因分析

### 3.1 复合 loss 的多目标权衡误入歧途

CompositeRobustLoss 同时优化 5 个归一化目标（Sortino 0.30 + Calmar 0.25 + PF 0.20 + 胜率 0.15 + 连续亏损 0.10）。优化器为在训练期"平衡"这些目标，选择了与策略本质逻辑相悖的参数：

| 参数 | v1（让利润奔跑） | v2（平衡但失效） |
|------|------------------|------------------|
| `minimal_roi` 首层 | 0.035（低门槛，灵活） | 0.125（高门槛，利润奔跑但过久） |
| `csl_mid_ratio` | 2.4（中期止损宽） | 0.7（中期止损过紧，频繁止损） |
| `sell_fastx` | 82（晚止盈） | 64（早止盈） |
| `trail_tier3_profit` | 0.236 | 0.148 |

v2 的 `csl_mid_ratio=0.7`（中期 ATR 止损收紧到 0.7 倍）导致持仓中期频繁被止损平仓，截断了趋势跟随的利润 —— 这正是原始 Kelvin 失败的同类问题（过早平仓），复合 loss 反而重新引入了它。

### 3.2 核心洞察

对该策略（趋势跟随 + 均值回归混合），**纯 Sortino 优化出的"让利润奔跑"参数（延后 trailing、低 ROI 门槛）反而 OOS 泛化更好**。复合 loss 追求的训练期"多目标平衡"过拟合到训练分布，牺牲了策略的核心优势。

**多目标 loss ≠ 更稳健**。在本案例中，单目标 Sortino 的解 OOS 更稳。

### 3.3 v2 训练期回撤反而更高

v2 训练 DD 45.15% > v1 的 32.48%。复合 loss 的回撤惩罚（阈值 20%）未能有效控制训练期回撤，因为其他目标的权重总和引导优化器接受了高回撤解。

## 4. 最终建议

### 推荐使用 v1（EfutureLongKelvin_v1）

- OOS 全面最优：收益 21.55%、Sortino 2.55、PF 1.34、DD 11.84%
- OOS 超越对比策略 Long（Long OOS 收益 15.60%）
- 参数已固化到 v1.py，自包含可独立运行
- OOS Sortino 保留率 46% 虽略低于 50% 阈值，但绝对 Sortino 2.55 优秀

### v2 处理

- v2（EfutureLongKelvin_v2）OOS 表现差，**不推荐使用**
- 保留 v2 文件与 v2.json 作为实验记录（复合 loss 在本策略上失效的实证）
- 如需进一步提升 OOS 保留率，不应走复合 loss 路线，应考虑：
  1. 滚动窗口多周期验证（skill 完整流程），选多窗口稳定解
  2. 缩短训练窗口（更近期数据，减少 regime 漂移）
  3. 在 v1 参数邻域做精细网格 + OOS 直接筛选（而非训练期 loss）

## 5. 文件说明

- 推荐策略：`user_data/strategies/EfutureLongKelvin_v1.py`（自包含，参数已固化）
- v1 参数快照：`user_data/strategies/EfutureLongKelvin_v1.json`
- 实验策略：`user_data/strategies/EfutureLongKelvin_v2.py` + v2.json（不推荐）
- 复合 loss：`user_data/hyperopts/composite_robust_loss.py`（实验用）
- 原始策略 `EfutureLongKelvin.py` 全程未修改
