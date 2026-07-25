# enew 谱系

本线为 ENEW 策略线预留位置，当前目录仅含 `.gitkeep`，无已晋级版本。

## 实验产物

ENEW 的 autoresearch 实验产物保留在 `strategies/experiments/autoresearch_enew/`：

| 实验号 | 说明 |
|---|---|
| ENEW_E0001 | smoke 验证候选（regression，已丢弃） |
| ENEW_E0005 | 实验迭代候选 |
| ENEW_E0023 | 低回撤候选，实盘收益/Sharpe 不如 baseline |
| ENEW_E0036 | 实验迭代候选 |

实验过程与 baseline 数据见 `user_data/autoresearch/README.md` 的 ENEW Runbook。

## 晋级规则

按规范，实验产物被采纳时才复制进本目录并改名升代（`ENEW_v1.py`）。当前无版本达到晋级标准。

**注意**：运行 ENEW 进化前需自备 base config——原 `user_data/config_enew_backtest.json` 当前缺失，详见 `user_data/autoresearch/README.md` 说明。
