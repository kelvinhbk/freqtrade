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
