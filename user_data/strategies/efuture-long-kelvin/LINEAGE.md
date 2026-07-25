# efuture-long-kelvin 谱系

谱系提取自 `EfutureLongKelvin_optimization_report.md`（2026-06-25）。完整数据见该报告。

| 版本 | 来源 | OOS 结果 |
|---|---|---|
| v1 | 基线：纯 Sortino loss，"让利润奔跑"（stoploss -0.287 + custom_stoploss + 低门槛 roi），3x | 滚动 10 窗口 CV=0.651，9/10 盈利；4-holdout 稳健 score 48.94 |
| v2 | v1 复合 loss 尝试 | 失败（复合 loss 不可行，详见报告与记忆 composite-crypto-loss-stoploss-trap） |
| v3-v11（含 v10） | 阶段一：单 OOS 窗口循环 20 轮，v1 邻域 hyperopt + top-5 单 OOS 筛选 | 滚动验证证明全部过拟合："best" v10 单 OOS 23.85%，但 CV=1.135，2 窗口亏损 DD 45.8%。v3-v11 弃用 |
| v12（3x） | 阶段二：多 holdout 稳健循环 8 轮，top-15 × 4 holdout 稳健 score（iter 3 产出，score 56.65 > v1 48.94） | 滚动 10 窗口 CV=0.433，10/10 盈利，全面优于 v1；但最大 DD 38.4% 过大，已删 |
| v12_2x（2x） | 阶段三：降杠杆 3x→2x（杠杆不影响信号参数，直接复用 v12 参数） | CV=0.470，10/10 盈利，最大 DD 30.0%，worst trade -6.8%。**最终实盘版** |

## 说明

- `EfutureLongKelvin_v12_2x_lock.py/.json` 为 v12_2x 的锁定变体；`_loop_state.json` 保存优化历史（含各版本参数，可重建已删变体如 v12_2x_prot）。
- v12_2x_prot（2x + MaxDrawdown/StoplossGuard）已删：DD 仅降到 26.4% 但 CV 恶化到 0.573。
- 回测/循环 config：`configs/backtest/EfutureLongKelvin.json`；编排脚本在 `scripts/`（run_efuture_loop.py、run_robust_loop.py、rolling_validate.py、gen_efuture_search.py）。
