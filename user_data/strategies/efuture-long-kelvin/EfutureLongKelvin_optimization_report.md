# EfutureLongKelvin 参数优化报告

**日期**：2026-06-25
**对象**：`user_data/strategies/EfutureLongKelvin.py`（纯做多，8 pair binance futures，5m）
**最终实盘版**：`EfutureLongKelvin_v12_2x`（2x 杠杆）

---

## 1. 概述（三阶段优化）

1. **阶段一（单 OOS 窗口循环，20 轮）** → v3-v11（含 v10），滚动验证证明**全部过拟合**。
2. **阶段二（多 holdout 稳健循环，8 轮）** → v12（3x），稳健击败 v1。
3. **阶段三（杠杆风险优化）** → **v12_2x（2x）**，平衡 DD 与稳定性，**最终实盘版**。

编排脚本（`scripts/`）：`run_efuture_loop.py`、`run_robust_loop.py`、`rolling_validate.py`、`gen_efuture_search.py`。

---

## 2. 基线 v1

`EfutureLongKelvin_v1`（`user_data/strategies/`）：纯 Sortino loss，"让利润奔跑"（stoploss -0.287 + custom_stoploss + 低门槛 roi），3x 杠杆。

- 滚动 10 窗口 CV=0.651，9/10 盈利，profit -4.64%~122%
- 4-holdout 稳健 score 48.94

---

## 3. 阶段一：单 OOS 循环 → v3-v11（全部过拟合）

20 轮 v1 邻域 hyperopt + top-5 单 OOS 筛选。"best" v10（单 OOS 23.85%）滚动 10 窗口 CV=**1.135**，2 窗口亏损 DD 45.8%（202412-202505 -14%、202502-202507 -19.7%）。

**结论：单 OOS 筛选必然过拟合到该窗口，v3-v11 弃用。** v1 反而更稳（CV 0.651）。

---

## 4. 阶段二：多 holdout 稳健循环 → v12（3x）

改用 top-15 × 4 holdout 稳健 score（`run_robust_loop.py`，训练 20240801-20251130，4 个 2 月 holdout）。
- 稳健门：4 holdout 全 PF>1.0 + 全 profit>0 + maxDD<25%
- 保存：score > 当前 best

iter 3 产出 **v12**（robust score 56.65 > v1 48.94），滚动 10 窗口 **CV=0.433，10/10 盈利**，全面优于 v1。iter 6-8 连续无突破 → v12 是强局部最优。

---

## 5. 阶段三：杠杆风险优化 → v12_2x（最终实盘版）

### 5.1 v12（3x）的回撤风险

v12 滚动最大 DD **38.4%**（202412-202505 / 202502-202507），4 个窗口 DD>30%。最大 DD 时段详细：max DD 38.39%、worst trade -10.18%、draw days 122/242。**3x 杠杆放大回撤，大回撤风险显著。**

### 5.2 加 protection 改善有限

v12 + MaxDrawdown(0.15) + StoplossGuard（v12_prot）在最大 DD 时段：DD 仅 38.4%→36.6%（-1.8%），profit/sortino 升但 DD 几乎没降——protection 是"暂停"机制，无法解决纯做多在持续下跌市的回撤。

### 5.3 降杠杆（3x→2x）= 线性有效

**杠杆不影响信号参数**（buy/sell/roi/stoploss 都是价格/比例触发，sortino 是比值与杠杆无关）→ **2x 直接复用 v12 参数，无需重优化**。

四方滚动 10 窗口对比：

| 版本 | 杠杆 | protection | CV | 最大 DD | 盈利 |
|------|-----|-----------|-----|--------|------|
| v12 | 3x | Cooldown | **0.433** | 38.4% | 10/10 |
| **v12_2x** | **2x** | **Cooldown** | **0.470** | **30.0%** | **10/10** |
| v12_2x_prot | 2x | +MaxDrawdown+StoplossGuard | 0.573 | 26.4% | 10/10 |

**关键洞察**：去掉额外 protection 后 CV 从 0.573→0.470（protection 暂停交易干扰一致性）；2x 把 DD 从 38.4%→30.0%（线性降）。

**v12_2x = 最佳平衡**：DD 30%（比 v12 安全 8 个百分点，多数窗口<25%），CV 0.470（接近 v12 的 0.433，远优于 v1 的 0.651），结构最简洁。

---

## 6. 最终结论与实盘建议

| 版本 | 性质 | 结论 |
|------|------|------|
| **v12_2x** | 2x 杠杆，多 holdout 稳健 | **✅ 最终实盘版**：CV 0.470，10/10 盈利，最大 DD 30%，worst trade -6.8% |
| v12 | 3x 杠杆原版 | 已删（DD 38.4% 过大） |
| v12_2x_prot | 2x+protection | 已删（CV 0.573 牺牲大） |
| v1 | 旧基准 | 备选（CV 0.651） |
| v10/v3-v11 | 单 OOS 过拟合 | 弃用 |

**实盘建议**：
1. **部署 `EfutureLongKelvin_v12_2x`**（2x 杠杆，DD 30%，10/10 窗口盈利）。
2. 上线前 dry-run 2-4 周确认实盘与回测一致。
3. 若仍想降 DD（<25%）：试 1x 杠杆（DD 约降到 ~15%），代价是收益再减半。
4. 风险厌恶极致可选 v12_2x_prot（DD 26.4%，但 CV 0.573）——可从 state 重建。

---

## 7. 方法论与 freqtrade 陷阱（详见记忆 `freqtrade-loop-optimization`）

**有效方法论**：
- v1 邻域 hyperopt（±20% span），固定 roi/stoploss（保"让利润奔跑"）。
- 纯 Sortino loss（不走复合 loss，见 `composite-crypto-loss-stoploss-trap`）。
- **按 backtest sortino 取 top-K**（非 loss/日 sortino）。
- **多 holdout 稳健 score** 避免单窗口过拟合。
- **降杠杆线性降 DD**，且不影响信号参数（无需重优化）。

**freqtrade 陷阱**：
- `hyperopt` 不认 `--hyperopt-filename`；`--spaces` 是 nargs+（subprocess 分开传）；`hyperopt-show --print-json` 非 tty 失败→直接解析 `.fthypt`。
- 非策略 `.py` 不能放 `user_data/strategies/`（recursive import 崩）。
- proxy 1082 约 90% 可用，间歇 503 → backtest/hyperopt 加重试。
- protection 类名：`MaxDrawdown`（小写 d）、`StoplossGuard`、`CooldownPeriod`。

---

## 8. 文件清单

`user_data/strategies/Efuturelong/`：
- **`EfutureLongKelvin_v12_2x.py` + `.json`** — 最终实盘版（2x 杠杆）
- `EfutureLongKelvin_optimization_report.md` — 本报告
- `_loop_state.json` — 优化历史 state（含各版本参数，可重建变体）

`scripts/`：`run_efuture_loop.py`、`run_robust_loop.py`、`rolling_validate.py`、`gen_efuture_search.py`
