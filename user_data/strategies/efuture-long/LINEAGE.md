# efuture-long 谱系

EfutureLong 非 Kelvin 系列，与 efuture-long-kelvin 线相互独立。以下仅记录文件与 git 历史可证实的事实。

| 版本 | 来源 | OOS 结果 |
|---|---|---|
| v1 | 本线基线（`EfutureLong_v1.py/.json`） | 未记录 |
| v2 | v1 风控优化（commit c430a61f3，2026-06-11）：参数化盈利保护阈值、加宽 custom_stoploss、优化 sell 参数 | 目标为将回撤从 45% 降到 25%；提交信息记录训练 Sortino 1.81→3.07、Sharpe 2.54→3.64、PF 1.30→1.39，OOS 验证 v2 全时段优于 v1 |
| v4 | 后续迭代（`EfutureLong_v4.py/.json`），附 hyperopt 参数文件 `EfutureLong_v4_hyperopt.json` | 未记录 |

## 说明

- `EfutureLong.py` 为无代际文件，与 v1/v2/v4 的关系未在 git 历史中明确记录。
- 本线当前无专用 config，未在 configs/ 清单中登记。
