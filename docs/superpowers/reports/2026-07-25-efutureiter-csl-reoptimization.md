# EfutureIter csl_* 重新优化报告（T15）

日期：2026-07-25
执行：controller 直接执行（strategy-parameter-optimization 流程，范围经用户确认：仅 csl_*）
前置：T13 custom_stoploss 修复（commit 4e593c13d）+ A/B 报告（docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md）

## TL;DR

1. **csl 优化在训练窗口有效**：最优 csl_mid_ratio=2.1 / csl_late=-0.059，训练亏损减半（-17.24% → -8.71%），trailing 止损次数 29 → 9。
2. **但 OOS 不验证**：W1 窗口优化版 -2.06%，比现参数修复版（-0.94%）更差——训练/OOS 方向相反，且样本量极小（W1 仅 14 笔）。
3. **更深层发现**：bug 版、修复版、优化版在长训练窗口（26 个月）**全部亏损**（-17.18% / -17.24% / -8.71%）。T13 A/B 的"bug 歪打正着"是 W1/W2 有利窗口的假象——长窗口下止损公式差异几乎不影响总盈亏（-17.18% vs -17.24%），**策略当前参数化的经济基础本身是负的**。

## 设置

- 变体：`EfutureIterCslOpt`（commit eb770b6ad）——修复版副本，38 个参数中仅 csl_mid_ratio/csl_late 可优化，其余锚定现值
- hyperopt：`--spaces sell --epochs 300 -j 8 --hyperopt-loss SortinoHyperOptLossDaily --timerange 20240101-20260228 --random-state 42 --min-trades 30`（300/300 完成，日志 /tmp/t15_hyperopt.log）
- 对照组：A = EfutureIterBuggy（T13 修复前代码，从 git 0858d9381 恢复）；B = 修复版现参数（csl 1.1/-0.046）；C = 修复版优化参数（csl 2.1/-0.059）
- 数据：本地 feather，8 对，无网络访问

## hyperopt 地形发现

- 300 epochs 中最优 loss 0.17257（epoch 9，NSGAIII 采样），对应 csl_mid=2.1 / csl_late=-0.059
- **csl_late 无优化信号**：top-8 epochs loss 完全相同但 csl_late 各异（-0.019~-0.077）。原因：平均持仓 23 分钟，极少交易活过 240 分钟进入 late 阶段（`max(csl_late, atr_pct)` 不生效）
- **csl_mid 信号微弱**：30-240 分钟阶段也只覆盖少数交易；0-30 分钟阶段由硬编码 csl_initial=-0.10 主导（不在优化空间内）

## 三方对比

### 训练窗口（20240101-20260228，789 天）

| 指标 | A buggy | B fixed 现参 | C fixed 优参 |
|---|---|---|---|
| 总收益 | -17.18% | -17.24% | **-8.71%** |
| 交易数 | 97 | 97 | 97 |
| Profit Factor | 0.66 | 0.67 | **0.81** |
| Sortino | -0.20 | -0.24 | **-0.11** |
| 最大回撤 | 37.00% | 32.76% | **30.81%** |
| trailing 止损 | 18 次 | 29 次 | **9 次** |

### OOS W1（20260301-20260520）

| 指标 | A buggy（T13） | B fixed 现参（T13） | C fixed 优参 |
|---|---|---|---|
| 总收益 | **+0.62%** | -0.94% | -2.06% |
| 最大回撤 | **3.39%** | 4.65% | 5.89% |
| trailing 止损 | 2 | 3 | 1 |
| 交易数 | 14 | 14 | 14 |

### W2 高波动（20241004-20250101，在训练窗口内）

| 指标 | A buggy（T13） | B fixed 现参（T13） | C fixed 优参 |
|---|---|---|---|
| 总收益 | **+3.22%** | -0.88% | +0.99% |
| 最大回撤 | **3.11%** | 6.71% | 5.12% |
| 交易数 | 10 | 10 | 10 |

## 结论

1. csl 优化的训练窗口改善是真实的（PF 0.67→0.81、止损次数 29→9、回撤收窄），方向符合修复语义（更宽的 mid 阶段止损 = 更少误触发）。
2. 但 OOS W1 方向相反（-2.06% vs -0.94%），叠加 hyperopt 地形平坦（csl_late 无效），**"仅 csl_* 优化能找回收益"的假设不成立**。
3. 根本问题：策略在长窗口全变体亏损（PF < 1）。W1/W2 的盈利是区间运气。T10 实盘启动的前提（"优化后参数可用"）**当前不成立**——不是参数问题，是策略经济基础问题。
4. 样本量全线不足（训练 97 笔/26 个月 = 0.12 笔/天，OOS 14 笔），上述所有差异的统计意义都弱，但"长窗口全变体 PF < 1"是三个独立参数化下的一致信号。

## 决策选项（用户裁定）

- ① 写回 csl 2.1/-0.059 到 EfutureIter.json：接受"训练改善但 OOS 未验证"的参数（不推荐单独做）
- ② 保持现参数，不写回
- ③ 扩大优化范围：sell/roi/入场全空间重新优化（针对"策略经济基础为负"的对因治疗，但过拟合风险高、耗时长）
- ④ 暂停 efuture-iter 线实盘计划（T10），先立项评估策略存续（重组/重写/退役）

## 产物与清理

- 保留：`EfutureIterCslOpt.py/json`（优化参数在 json 中，待裁定）
- 待清理：`EfutureIterBuggy.py/json`（分析用临时变体，可从 git 0858d9381 重现，裁定后删除）
- hyperopt 结果：`user_data/hyperopt_results/strategy_EfutureIterCslOpt_2026-07-25_23-37-18.fthypt`（300 epochs）
