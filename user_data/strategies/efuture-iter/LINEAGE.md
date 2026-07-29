# efuture-iter 谱系

| 版本 | 来源 | OOS 结果 |
|---|---|---|
| AutoResearch_iter0002_E0010_E0014_E0033 | autoresearch0515 实验链 E0010→E0014→E0033 | 训练 +429.5%/测试 +42.63%（2026-05-25 审查，custom_stoploss bug 未修复，见审查报告） |
| EfutureIter | E0033 改名 + 参数迭代 | 实盘运行中（2026-07 起） |

## 说明

- 谱系起点 `AutoResearch_iter0002_E0010_E0014_E0033.py/.json` 保留在本目录，审查报告见 `E0033_审查报告.md`。
- **已知未修复缺陷**：E0033 审查报告（2026-05-25）指出的 `custom_stoploss` 公式错误（30-240 分钟阶段实际止损约 -0.83% 而非设计的 -1.65%）原样存在于实盘 `EfutureIter.py`。是否修复、何时修复由用户决策（独立于本次重组）。
- 实盘 config：`configs/live/EfutureIter.json`（dry_run=false，2026-07-25 裁定为真身）；模拟盘：`configs/dryrun/EfutureIter.json`。

## 2026-07-25 custom_stoploss 行为变更

- 修复 :320-326 公式错误（混入 current_rate，实际触发价为 sqrt(desired×open)），改为 `return leverage_val * desired_pct`（引自 E0033 审查报告）。
- A/B 依据：docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md（W1 20260301-20260520 / W2 20241004-20250101 双窗口）。
- 决策门裁定范围：①仅 stoploss；连带项明细见报告附录。
- 注意：所有历史回测/hyperopt 结论基于错误止损逻辑；csl_* 参数再优化列为后续任务。
- 部署：并入 T10（待 T14 密钥轮换后启动实盘）。

## 2026-07-29 T15 裁定：④暂停 efuture-iter 线实盘计划（T10）

- T15 csl_* 重优化（报告 `docs/superpowers/reports/2026-07-25-efutureiter-csl-reoptimization.md`，commit `76ea8187b`）：最优 csl_mid_ratio=2.1 / csl_late=-0.059 训练窗口减半亏损（-17.24%→-8.71%，PF 0.67→0.81）但 OOS W1 更差（-2.06% vs -0.94%）。
- 关键发现：bug 版/修复版/优化版三个变体在 26 个月长窗口**全部亏损（PF<1）**——T13 的"bug 歪打正着"是 W1/W2 有利窗口假象；策略经济基础为负，不是参数问题。
- 用户裁定（2026-07-29）：**④暂停 T10 实盘启动，立项评估策略存续（重组/重写/退役）**。csl 优化参数不写回 EfutureIter.json（保留现值 csl_mid 1.1/csl_late -0.046）。
- 清理：分析用临时变体 `EfutureIterBuggy.py/json` 与 `EfutureIterCslOpt.py/json` 已删除（可从 git commit `76ea8187b` 恢复；优化参数同时记录在报告与 `user_data/hyperopt_results/strategy_EfutureIterCslOpt_2026-07-25_23-37-18.fthypt`）。
- 注意：本线实盘 config（`configs/live/EfutureIter.json` 与 `configs/dryrun/EfutureIter.json`）在评估结论出来前不得用于启动实盘。
