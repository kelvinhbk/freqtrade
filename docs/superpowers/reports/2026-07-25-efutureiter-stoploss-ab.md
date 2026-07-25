# EfutureIter custom_stoploss 修复 A/B 对比

- 日期：2026-07-25
- 执行者：SDD Task 1 实现 subagent（impl-t13-1）
- 分支：kelvin
- Spec：docs/superpowers/specs/2026-07-25-efutureiter-custom-stoploss-fix-design.md
- 原始日志：/tmp/t13_w1_buggy.log、/tmp/t13_w1_fixed.log、/tmp/t13_w2_buggy.log、/tmp/t13_w2_fixed.log（冒烟：/tmp/t13_smoke_fix.log）

## 背景与修复公式

`EfutureIter.py:320-326` 的 `custom_stoploss` 在返回值公式中混入 `current_rate`，数学上实际触发价格变为 `sqrt(desired_stop_price × open_rate)` 而非设计的 `desired_stop_price`（2026-05-25 E0033 审查报告首次指出，2026-07-25 重组排查确认 bug 原样存在于实盘策略中）。

修复前（buggy，EfutureIter）：

```python
leverage_val = trade.leverage or 1.0
if trade.is_short:
    desired_stop_price = trade.open_rate * (1 - desired_pct)
    return leverage_val * (1 - desired_stop_price / current_rate)
else:
    desired_stop_price = trade.open_rate * (1 + desired_pct)
    return leverage_val * (desired_stop_price / current_rate - 1)
```

修复后（fixed，EfutureIterFix）：

```python
leverage_val = trade.leverage or 1.0
return leverage_val * desired_pct
```

量化影响（引自 spec / E0033 审查报告，leverage=3）：

| 阶段 | desired_pct | 预期价格移动 | 实际触发价格移动（buggy） | 杠杆后亏损（buggy） |
|------|-------------|--------------|---------------------------|---------------------|
| 0-30 min | -10.0% | -10.0% | -5.13% | -15.4% |
| 30-240 min | ~-1.65% | -1.65% | **-0.83%** | **-2.5%** |
| 240 min+ | -4.4% | -4.4% | **-2.22%** | **-6.7%** |

修复变体与现版的全部差异（diff 验证，除下述外字节一致）：

- `class EfutureIter(IStrategy):` → `class EfutureIterFix(IStrategy):`（:19）
- 上述 stoploss 块 7 行 → 2 行（:321-326 → :321）
- `EfutureIterFix.json`：`"strategy_name": "EfutureIter"` → `"EfutureIterFix"`（:2）

## 回测设置

- Config：`user_data/configs/backtest/EfutureIter.lineage.json`（futures / isolated，stake unlimited，max open trades 5，StaticPairList 8 对：DOT/ENJ/RENDER/SOL/TAO/TON/WLD/ZEC）
- 杠杆：策略 `leverage()` 固定 3.0
- 参数文件：buggy 跑加载 `EfutureIter.json`，fixed 跑加载 `EfutureIterFix.json`（两文件仅 strategy_name 一行之差；四次日志均含对应 `Loading parameters from file` 行）
- W1 近期 OOS：`20260301-20260520`（80 天，8 对等权 avg range 0.404%）
- W2 高波动：`20241004-20250101`（89 天，avg range 0.526%，全样本最高）
- 全程本地 feather 数据，无网络访问；四次回测均 0 ERROR、均产出完整 SUMMARY METRICS

## W1 近期 OOS（20260301-20260520）对比

| 指标 | buggy | fixed | 差值（fixed-buggy） |
|---|---|---|---|
| Total trades | 14 | 14 | 0 |
| Win / Draw / Loss | 12 / 0 / 2 | 11 / 0 / 3 | -1W / +1L |
| Win rate | 85.7% | 78.6% | -7.1pp |
| Total profit % | 0.62% | -0.94% | -1.56pp |
| Absolute profit | 6.157 USDT | -9.371 USDT | -15.528 USDT |
| Max drawdown | 34.191 USDT (3.39%) | 46.735 USDT (4.65%) | +12.544 USDT (+1.26pp) |
| Sortino / Sharpe | 0.09 / 0.14 | -0.11 / -0.17 | 转负 |
| Profit factor | 1.17 | 0.82 | -0.35 |
| stop_loss 退出 | 0 | 0 | 0 |
| trailing_stop_loss 退出 | 2（占 14.3%，avg -9.3%） | 3（占 21.4%，avg -7.9%） | +1 |
| roi 退出 | 1 | 1 | 0 |
| fastk_profit_sell 退出 | 11 | 9 | -2 |
| trend_exit_long 退出 | 0 | 1 | +1 |
| Avg duration 胜 / 负 / 总 | 0:21 / 0:30 / 0:22 | 0:17 / 0:38 / 0:22 | 负单 +8min |
| Worst trade | -17.13% | -23.47% | -6.34pp |

EXIT REASON STATS（buggy）：fastk_profit_sell 11 / roi 1 / trailing_stop_loss 2 / TOTAL 14
EXIT REASON STATS（fixed）：fastk_profit_sell 9 / roi 1 / trend_exit_long 1 / trailing_stop_loss 3 / TOTAL 14

## W2 高波动（20241004-20250101）对比

| 指标 | buggy | fixed | 差值（fixed-buggy） |
|---|---|---|---|
| Total trades | 10 | 10 | 0 |
| Win / Draw / Loss | 7 / 0 / 3 | 7 / 0 / 3 | 0 |
| Win rate | 70.0% | 70.0% | 0 |
| Total profit % | 3.22% | -0.88% | -4.10pp |
| Absolute profit | 32.247 USDT | -8.842 USDT | -41.089 USDT |
| Max drawdown | 31.712 USDT (3.11%) | 68.121 USDT (6.71%) | +36.409 USDT (+3.60pp) |
| Sortino / Sharpe | 1.29 / 0.59 | -0.10 / -0.10 | 转负 |
| Profit factor | 1.89 | 0.88 | -1.01 |
| stop_loss 退出 | 0 | 0 | 0 |
| trailing_stop_loss 退出 | 3（占 30.0%，avg -6.0%） | 3（占 30.0%，avg -12.53%） | 次数 0，单笔亏损 +6.53pp |
| roi 退出 | 1 | 1 | 0 |
| fastk_profit_sell 退出 | 6 | 6 | 0 |
| Avg duration 胜 / 负 / 总 | 0:16 / 0:30 / 0:20 | 0:16 / 0:27 / 0:19 | 基本持平 |
| 负单 duration 范围 | 0:30 – 0:30 | 0:05 – 0:45 | 出现 0:05 初始阶段止损 |
| Worst trade | -8.30% | -25.88% | -17.58pp |

EXIT REASON STATS（buggy）：fastk_profit_sell 6 / roi 1 / trailing_stop_loss 3 / TOTAL 10
EXIT REASON STATS（fixed）：fastk_profit_sell 6 / roi 1 / trailing_stop_loss 3 / TOTAL 10

## 止损行为分析

口径说明：四次回测中 exit_reason 为 `stop_loss` 的行均为 0；所有止损族退出都以 `trailing_stop_loss` 呈现。原因是 `custom_stoploss` 动态调整止损价后，freqtrade 将触发标记为 trailing_stop_loss。下文的"止损触发"均指该行。

与审查报告预期的对照（30-240min 阶段实际触发从 ~0.83% 价格移动恢复到设计 1.65%）：

1. **触发更晚（止损变宽），方向吻合。** W1 负单平均持仓从 0:30 延长到 0:38（0:35-0:45），落在 30-240min 阶段内——价格需要反向走 1.65%（设计）而不是 0.83%（bug）才触发，耗时更长。W1 止损次数 2→3：bug 版中一笔在更紧止损下以 -1.47% 小额出场的交易，修复后走到更宽止损 / 趋势退出（新增 1 次 trend_exit_long、1 次 trailing_stop_loss）。
2. **单次止损亏损幅度上升，方向吻合。** W2 止损单笔平均亏损 -6.0% → -12.53%；mid 阶段设计 1.65%×3 杠杆 ≈ 4.95% 杠杆亏损，对比 bug 版的 2.5%，实测方向与量级一致。W2 出现一笔 0:05 出场的负单（0-30min 初始阶段），worst trade -25.88%——对应初始阶段设计止损 -10% 价格移动（杠杆后约 -30% 上界），而 bug 版该阶段有效触发在 -5.13% 价格移动（约 -15.4%）。修复后初始阶段止损同样按设计变宽。
3. **触发次数本身未出现数量级变化**（W1 2→3，W2 3→3）。两个窗口交易总数都很少（14 / 10 笔），止损样本仅 2-3 笔/窗口，占比变化（14.3%→21.4%、30%→30%）在此样本量下不具备统计意义，仅方向性参考。

综合：实测方向与审查报告预期一致——修复后止损在全部三个阶段都恢复为更宽的设计距离，表现为触发更晚、单笔止损亏损更大。

## 结论与建议

1. **修复达到设计语义。** 两窗口入场完全一致（交易数 14/14、10/10，仅 custom_stoploss 差异），全部结果差异来自止损路径；止损行为变化方向与 spec 量化表一致（触发更晚、单笔亏损更大、负单持仓更长）。
2. **在当前 csl_* 参数下，修复版两窗口收益均显著低于 bug 版**（W1 +0.62%→-0.94%，W2 +3.22%→-0.88%），最大回撤同步放大（W1 3.39%→4.65%，W2 3.11%→6.71%）。即：现有参数（在错误止损语义下 hyperopt 得出）叠加 bug 的"全面更紧止损"，在这两个窗口恰好构成了更优的事后组合——bug 歪打正着。spec 决策门条款已明确：不得以"修复后收益下降"为由无限期保留错误逻辑而不做记录。
3. **建议将 csl_\* 参数再优化（重新 hyperopt）列为后续任务。** 现参数是在 sqrt 失真语义下调优的，A/B 直接对比对 fixed 版并不公平；修复落地后应在新语义下重新寻优 csl_initial / csl_mid_ratio / csl_late（及 stoploss=-0.19 的协同）。
4. **样本量提醒**：两窗口合计 24 笔交易/版本，结论 2 的收益差异幅度不宜外推到其他行情段。

## 决策门选项（供用户裁定）

1. **仅修 stoploss**：EfutureIter.py 应用两行修复，连带项不动。
2. **stoploss + 连带项**：同时处理附录三项（trend_adverse 改名、删 3 个未用参数、enter_tag 赋值），均已论证行为等价。
3. **暂不部署修复**：报告与变体留存，实盘继续以 bug 语义运行（需知悉：实盘正以非设计意图的止损行为运行，且 0-30min 阶段设计上界 -30% 杠杆亏损在 bug 下被压缩为 -15.4%，修复后单笔尾部亏损会变大）。

## 附录：连带项清单（已知事实 7 原文）

- `bearish` 变量命名语义相反：定义在 :344（short 分支，ema_50_uptrend and ema_200_uptrend）与 :346（long 分支），使用在 :377、:380（`cci < -80 and bearish` / `cci > 80 and bearish`）。语义实为"趋势与持仓方向相反"，重命名为 `trend_adverse`（共 4 处：344、346、377、380）。逻辑碰巧正确，改名不改行为。
- 3 个定义后未使用的参数（grep `.value` 零命中）：`buy_volume_sma`（:62 起 DecimalParameter 块）、`sell_bb_middle_profit`（:102 起）、`buy_atr_ratio`（:126 起）。删除 .py 中定义块；**EfutureIter.json 保持不动**（孤儿条目 freqtrade 忽略，保留 hyperopt 历史记录）。
- enter_tag `+=` 改直接赋值：:234 `dataframe.loc[long_mr_conditions, "enter_tag"] += "long_mr"` → `= "long_mr"`。:213 已初始化整列为 ""，行为等价。
