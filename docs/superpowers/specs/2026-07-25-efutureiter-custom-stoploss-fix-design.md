# EfutureIter custom_stoploss 公式错误修复专项

日期：2026-07-25
状态：立项（待执行，独立于 user_data 重组计划）

## 背景

2026-05-25 的 `E0033_审查报告.md`（现位于 `user_data/TopStr/`，重组后归 `strategies/efuture-iter/`）指出 `AutoResearch_iter0002_E0010_E0014_E0033` 的 `custom_stoploss` 公式错误。2026-07-25 重组排查确认：**该 bug 未修复，且原样存在于当前实盘运行的 `EfutureIter.py:322-326`**（EfutureIter 是 E0033 的改名后代，dry_run=false 实盘中）。

## Bug 描述

位置：`user_data/strategies/EfutureIter.py:322-326`（重组后为 `strategies/efuture-iter/EfutureIter.py`）

```python
if trade.is_short:
    desired_stop_price = trade.open_rate * (1 - desired_pct)
    return leverage_val * (1 - desired_stop_price / current_rate)   # ← 错误
else:
    desired_stop_price = trade.open_rate * (1 + desired_pct)
    return leverage_val * (desired_stop_price / current_rate - 1)   # ← 错误
```

`custom_stoploss` 返回值应是"相对开仓价的止损距离"，但公式混入了 `current_rate`，数学上实际触发价格变为 `sqrt(stop_price * open_rate)` 而非设定的 `stop_price`。

量化影响（引自审查报告）：

| 阶段 | desired_pct | 预期价格移动 | 实际触发价格移动 | 杠杆后亏损 |
|------|-------------|--------------|------------------|------------|
| 0-30 min | -10.0% | -10.0% | -5.13% | -15.4% |
| 30-240 min | ~-1.65% | -1.65% | **-0.83%** | **-2.5%** |
| 240 min+ | -4.4% | -4.4% | **-2.22%** | **-6.7%** |

30-240 分钟阶段止损极度敏感：价格反向移动约 0.83% 即触发（设计意图为 1.65%）。**实盘正在以非设计意图的止损行为运行，且所有历史回测/hyperopt 结论都建立在错误止损逻辑上。**

## 修复方案（引自审查报告）

`desired_pct` 本身为负值，正确实现可简化为：

```python
return leverage_val * desired_pct
```

即删除 is_short 分支里的 `desired_stop_price` 中间计算与 current_rate 混入。

## 执行计划

1. **A/B 回测**：同一 OOS 窗口（建议最近 3 个月 + 一段历史高波动期），分别跑修复前/修复后版本，对比 profit、DD、胜率、止损触发次数分布
2. **决策门**：结果交用户裁定——若修复后显著变差，需判断是"bug 歪打正着"还是回测噪声；不得以"修复后收益下降"为由无限期保留错误逻辑而不做记录
3. **部署**：选停机窗口切换实盘；LINEAGE.md 记录此次修复为行为变更点
4. **参数再验证**：修复改变了止损语义，现 hyperopt 参数（csl_* 系列）可能不再最优，评估是否需要重新优化（可作为后续任务）

## 连带项（同一审查报告，是否一并处理由用户在第 2 步决策时裁定）

- bearish 变量命名语义相反（:343-346，仅命名问题，逻辑碰巧正确）
- 3 个定义后未使用的 hyperopt 参数（buy_volume_sma、buy_atr_ratio、sell_bb_middle_profit）
- enter_tag `+=` 拼接改直接赋值（:234）

## 约束

- 不与 user_data 重组计划（docs/superpowers/plans/2026-07-25-userdata-reorganization.md）同批部署，避免两个行为变更叠加增加归因难度
- 修复 commit 单独提交，message 注明行为变更与回测依据
- 实盘切换遵循与重组 Task 10 相同的停机窗口纪律
