# EfutureIter custom_stoploss 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 A/B 回测验证并修复 EfutureIter custom_stoploss 公式错误（修复经用户决策门裁定后落地）。

**Architecture:** 创建修复变体策略（EfutureIterFix，仅 stoploss 三行差异）与现版并行回测两个窗口，产出对比报告 → 用户决策门 → 按裁定范围应用修复、删除变体、验证一致性、更新 LINEAGE。

**Tech Stack:** freqtrade backtesting（本地 feather 数据）、Python 3.11 (.venv)。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-efutureiter-custom-stoploss-fix-design.md`
- 修复 commit 单独提交，message 注明行为变更与回测依据。
- 本计划**不含实盘部署**：部署并入 T10（blocked by T14 密钥轮换）。
- 参数再验证（csl_* 重新 hyperopt）是后续任务，不在本计划；仅在报告中给出建议。
- 回测全程走本地数据，不访问交易所；若回测触发网络访问报错立即停止上报。
- 遵循 user_data 组织规范（CLAUDE.md "user_data 组织规范" 节）：谱系记录写 `user_data/strategies/efuture-iter/LINEAGE.md`。
- Conventional commits；工作分支 kelvin，直接提交（与重组同一模式）。
- 密钥纪律：不得读取/输出 `user_data/configs/secrets.local.json` 与 `archive/backup/` 下文件内容。

## 已知事实（planning 阶段已核实，执行者无需重新发现）

1. **Bug 位置**：`user_data/strategies/efuture-iter/EfutureIter.py:320-326`，当前代码（逐字）：
   ```python
           leverage_val = trade.leverage or 1.0
           if trade.is_short:
               desired_stop_price = trade.open_rate * (1 - desired_pct)
               return leverage_val * (1 - desired_stop_price / current_rate)
           else:
               desired_stop_price = trade.open_rate * (1 + desired_pct)
               return leverage_val * (desired_stop_price / current_rate - 1)
   ```
   `desired_pct` 为负值（如 csl_initial=-0.10），custom_stoploss 应返回相对开仓价的止损距离；公式混入 current_rate 导致实际触发价格变为 sqrt(desired_stop_price × open_rate)。
2. **修复公式**（引自 E0033 审查报告，spec 批准）：删除整个 if/else，替换为：
   ```python
           leverage_val = trade.leverage or 1.0
           return leverage_val * desired_pct
   ```
3. **参数文件机制**（freqtrade/strategy/hyper.py:101-116）：params JSON 与策略 .py 同目录同基名（`__file__` 派生），且 JSON 内 `strategy_name` 字段必须等于类名。EfutureIter.json 的 strategy_name 为 "EfutureIter"。优化值：csl_late=-0.046、csl_mid_ratio=1.1；csl_initial=-0.10 是硬编码类属性（非 Parameter）。
4. **leverage**：策略 `leverage()` 固定返回 3.0（EfutureIter.py:150-153）。
5. **回测 config**：`user_data/configs/backtest/EfutureIter.lineage.json`（dry_run=true，futures/isolated，StaticPairList 8 对：DOT/ENJ/RENDER/SOL/TAO/TON/WLD/ZEC）。
6. **数据与窗口**（planning 阶段扫描 8 对 5m feather，等权平均 (high-low)/close，滚动 90 天）：
   - W1 近期 OOS：`20260301-20260520`（avg range 0.404%；8 对全覆盖，RENDER 数据止于 2026-05-20 是窗口右界约束）
   - W2 高波动：`20241004-20250101`（avg range 0.526%，全样本最高；8 对全覆盖）
7. **连带项**（spec 第 52-56 行，决策门裁定是否一并处理）：
   - `bearish` 变量命名语义相反：定义在 :344（short 分支，ema_50_uptrend and ema_200_uptrend）与 :346（long 分支），使用在 :377、:380（`cci < -80 and bearish` / `cci > 80 and bearish`）。语义实为"趋势与持仓方向相反"，重命名为 `trend_adverse`（共 4 处：344、346、377、380）。逻辑碰巧正确，改名不改行为。
   - 3 个定义后未使用的参数（grep `.value` 零命中）：`buy_volume_sma`（:62 起 DecimalParameter 块）、`sell_bb_middle_profit`（:102 起）、`buy_atr_ratio`（:126 起）。删除 .py 中定义块；**EfutureIter.json 保持不动**（孤儿条目 freqtrade 忽略，保留 hyperopt 历史记录）。
   - enter_tag `+=` 改直接赋值：:234 `dataframe.loc[long_mr_conditions, "enter_tag"] += "long_mr"` → `= "long_mr"`。:213 已初始化整列为 ""，行为等价。
8. **执行环境**：无 worktree，直接在 kelvin 分支；macOS 无 `timeout` 命令（用后台+sleep+kill 替代，本计划不需要）；回测结果 zip 写入 user_data/backtest_results/（gitignored）。

---

### Task 1: A/B 回测对比（修复变体 + 双窗口四跑 + 对比报告）

**Files:**
- Create: `user_data/strategies/efuture-iter/EfutureIterFix.py`（EfutureIter.py 副本：类改名 + stoploss 修复，其余字节一致）
- Create: `user_data/strategies/efuture-iter/EfutureIterFix.json`（EfutureIter.json 副本：strategy_name 改名）
- Create: `docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md`（对比报告）

**Interfaces:**
- Consumes: 已知事实 1-6（bug 代码、修复公式、params 机制、config、窗口）。
- Produces: 对比报告（Task 2 决策门的输入）；EfutureIterFix 两个文件（Task 2 删除）。

- [ ] **Step 1: 创建修复变体**

```bash
cd /Users/kelvin/projects/freqtrade
cp user_data/strategies/efuture-iter/EfutureIter.py user_data/strategies/efuture-iter/EfutureIterFix.py
cp user_data/strategies/efuture-iter/EfutureIter.json user_data/strategies/efuture-iter/EfutureIterFix.json
```

编辑 `EfutureIterFix.py` 两处（其余不动）：

(a) 类改名（文件内唯一 class 定义行）：
```python
class EfutureIterFix(IStrategy):
```

(b) stoploss 修复——把已知事实 1 的 7 行（leverage_val 赋值 + if/else 共 6 行）整体替换为已知事实 2 的 2 行。

编辑 `EfutureIterFix.json`：`"strategy_name": "EfutureIter"` → `"strategy_name": "EfutureIterFix"`。

验证 diff 范围恰好是这三处：
```bash
diff user_data/strategies/efuture-iter/EfutureIter.py user_data/strategies/efuture-iter/EfutureIterFix.py
diff user_data/strategies/efuture-iter/EfutureIter.json user_data/strategies/efuture-iter/EfutureIterFix.json
```
预期：.py diff 只有类名行与 stoploss 块；.json diff 只有 strategy_name 行。

- [ ] **Step 2: 变体加载冒烟**

```bash
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-iter \
  --strategy EfutureIterFix --config user_data/configs/backtest/EfutureIter.lineage.json \
  --timerange 20260515-20260520 2>&1 | tee /tmp/t13_smoke_fix.log | grep -E "Loading parameters|Backtested|ERROR" | head -5
```

预期：日志含 `Loading parameters from file ...EfutureIterFix.json`（证明参数文件按类名正确加载）、产出 Backtested 行、无 ERROR。若 params 未加载（无该日志行），停止并检查 strategy_name 字段。

- [ ] **Step 3: 四跑 A/B 回测**

```bash
# W1 近期 OOS
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-iter --strategy EfutureIter    --config user_data/configs/backtest/EfutureIter.lineage.json --timerange 20260301-20260520 2>&1 | tee /tmp/t13_w1_buggy.log | tail -60
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-iter --strategy EfutureIterFix --config user_data/configs/backtest/EfutureIter.lineage.json --timerange 20260301-20260520 2>&1 | tee /tmp/t13_w1_fixed.log | tail -60
# W2 高波动
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-iter --strategy EfutureIter    --config user_data/configs/backtest/EfutureIter.lineage.json --timerange 20241004-20250101 2>&1 | tee /tmp/t13_w2_buggy.log | tail -60
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-iter --strategy EfutureIterFix --config user_data/configs/backtest/EfutureIter.lineage.json --timerange 20241004-20250101 2>&1 | tee /tmp/t13_w2_fixed.log | tail -60
```

预期：四次均无 ERROR、均产出完整 SUMMARY METRICS。若某次交易数为 0，在报告中如实记录（0 也是有效对比结果），不算失败。

- [ ] **Step 4: 提取指标**

从 4 个 log 的 `EXIT REASON STATS` 与 `SUMMARY METRICS` 段提取，每跑一组指标：

| 字段 | 来源 |
|---|---|
| Total/Daily Win Rate（trades 总数、胜/负/平、胜率） | SUMMARY METRICS |
| Total profit %（及 abs. profit USDT） | SUMMARY METRICS |
| Max drawdown（% 与 USDT） | SUMMARY METRICS |
| stop_loss 触发次数与占比 | EXIT REASON STATS 的 stop_loss 行 |
| 其余 exit reason 计数（custom_exit 各 tag、exit_signal、roi、force_exit 等） | EXIT REASON STATS |
| Avg. Duration（胜/负/总） | SUMMARY METRICS |

- [ ] **Step 5: 写对比报告并提交**

写 `docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md`，结构：

```markdown
# EfutureIter custom_stoploss 修复 A/B 对比

日期 / 执行者 / 分支
## 背景与修复公式（引用 spec 两公式 + 量化影响表）
## 回测设置（config、8 对、leverage 3.0、W1/W2 窗口与波动率依据）
## W1 近期 OOS（20260301-20260520）对比表（buggy vs fixed 逐项指标 + 差值列）
## W2 高波动（20241004-20250101）对比表（同上）
## 止损行为分析（stop_loss 次数/占比变化方向，与审查报告预期的对照：30-240min 阶段从 ~0.83% 触发恢复到设计 1.65%）
## 结论与建议（修复是否达到设计语义；csl_* 参数再优化是否建议作为后续任务）
## 决策门选项（①仅修 stoploss ②stoploss+连带项 ③暂不部署修复——供用户裁定）
```

连带项清单（已知事实 7）原文列入报告附录。

```bash
git add user_data/strategies/efuture-iter/EfutureIterFix.py user_data/strategies/efuture-iter/EfutureIterFix.json docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md
git commit -m "test(efuture-iter): custom_stoploss 修复变体与双窗口 A/B 回测对比报告"
```

---

### 决策门（控制器执行，非 subagent 任务）

Task 1 完成后，控制器把报告结论浓缩给用户，用 AskUserQuestion 裁定：
1. 修复范围：①仅 stoploss ②stoploss + 三项连带项（trend_adverse 改名、删 3 个未用参数、enter_tag 赋值）
2. 是否落地修复（若 A/B 显示修复后显著恶化，用户可选择暂不落地，报告留存）

**用户裁定前不得开始 Task 2。**

---

### Task 2: 应用修复 + 一致性验证 + 谱系记录（决策门通过后执行）

**Files:**
- Modify: `user_data/strategies/efuture-iter/EfutureIter.py`（stoploss 修复；范围②含连带项）
- Delete: `user_data/strategies/efuture-iter/EfutureIterFix.py`、`user_data/strategies/efuture-iter/EfutureIterFix.json`
- Modify: `user_data/strategies/efuture-iter/LINEAGE.md`（追加行为变更记录）

**Interfaces:**
- Consumes: Task 1 的 EfutureIterFix.py（修复参考）、A/B 报告（验证基准）、决策门裁定（范围①或②）。
- Produces: 修复后的 EfutureIter（等待 T10 部署）；LINEAGE 行为变更记录。

- [ ] **Step 1: 应用 stoploss 修复**

把 EfutureIter.py 中已知事实 1 的 7 行整体替换为已知事实 2 的 2 行。

- [ ] **Step 2:（仅范围②）连带项**

(a) `bearish` → `trend_adverse` 共 4 处（:344、:346、:377、:380，逐一替换，注意只改该变量名）：
```python
# :344（short 分支）
            trend_adverse = current_candle["ema_50_uptrend"] and current_candle["ema_200_uptrend"]
# :346（long 分支）
            trend_adverse = not current_candle["ema_50_uptrend"] and not current_candle["ema_200_uptrend"]
# :377 / :380
                if current_candle["cci"] < -80 and trend_adverse:
                if current_candle["cci"] > 80 and trend_adverse:
```

(b) 删除 3 个未使用参数的完整定义块（先 `grep -n -A3 "buy_volume_sma = \|sell_bb_middle_profit = \|buy_atr_ratio = "` 确认块边界，删整个 DecimalParameter(...) 块）：`buy_volume_sma`、`sell_bb_middle_profit`、`buy_atr_ratio`。EfutureIter.json 不动。

(c) :234 `+= "long_mr"` → `= "long_mr"`。

- [ ] **Step 3: 删除变体 + 编译检查**

```bash
git rm user_data/strategies/efuture-iter/EfutureIterFix.py user_data/strategies/efuture-iter/EfutureIterFix.json
.venv/bin/python -m py_compile user_data/strategies/efuture-iter/EfutureIter.py
```

- [ ] **Step 4: 一致性验证回测**

```bash
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-iter --strategy EfutureIter \
  --config user_data/configs/backtest/EfutureIter.lineage.json --timerange 20260301-20260520 2>&1 | tee /tmp/t13_w1_verify.log | tail -40
```

范围①：与 /tmp/t13_w1_fixed.log 对比，trades 数、Total profit %、stop_loss 次数应**完全一致**（代码路径相同）。范围②：连带项均经论证行为等价（改名/删未用参数/等价赋值），同样应完全一致；若不一致，逐行 diff EfutureIter.py 与已删除变体的 stoploss 段并查明原因，不得直接放过。

- [ ] **Step 5: LINEAGE 记录并提交**

`user_data/strategies/efuture-iter/LINEAGE.md` 末尾追加：

```markdown
## 2026-07-25 custom_stoploss 行为变更

- 修复 :320-326 公式错误（混入 current_rate，实际触发价为 sqrt(desired×open)），改为 `return leverage_val * desired_pct`（引自 E0033 审查报告）。
- A/B 依据：docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md（W1 20260301-20260520 / W2 20241004-20250101 双窗口）。
- 决策门裁定范围：<①仅 stoploss / ②stoploss+连带项，按实际填写>；连带项明细见报告附录。
- 注意：所有历史回测/hyperopt 结论基于错误止损逻辑；csl_* 参数再优化列为后续任务。
- 部署：并入 T10（待 T14 密钥轮换后启动实盘）。
```

```bash
git add user_data/strategies/efuture-iter/EfutureIter.py user_data/strategies/efuture-iter/LINEAGE.md
git commit -m "fix(efuture-iter): custom_stoploss 公式修复（行为变更，A/B 依据见报告）

修复 :320-326 混入 current_rate 的止损公式，恢复设计止损语义。
历史回测/hyperopt 结论基于错误止损逻辑，csl_* 参数再优化列为后续任务。
A/B 对比: docs/superpowers/reports/2026-07-25-efutureiter-stoploss-ab.md"
```

（范围②时 message 首行改为 `fix(efuture-iter): custom_stoploss 公式修复与连带清理（行为变更，A/B 依据见报告）`）
