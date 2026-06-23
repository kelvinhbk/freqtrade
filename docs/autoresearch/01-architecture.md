# AutoResearch 系统架构总览

## 1. 设计哲学

AutoResearch 是一个**分层协作**的策略自我进化系统，核心设计理念来自于 Andrej Karpathy 的 autoresearch 思想：

- **LLM 负责架构**：指标选择、风控框架、进出场逻辑结构
- **Hyperopt 负责参数**：贝叶斯优化搜索最优参数值
- **分层循环进化**：结构变异 → 参数优化 → 回测评估 → 择优保留

为什么分层？

| 能力 | LLM | Hyperopt |
|------|-----|----------|
| 参数搜索 | 弱（随机猜测） | 强（贝叶斯优化） |
| 结构创新 | 强（理解指标逻辑） | 无（只能调已有参数） |
| 过拟合控制 | 弱 | 强（搜索空间约束） |
| 速度 | 慢（API 调用） | 快（本地计算） |

## 2. 系统架构图

```
+-------------------+
|   evolve.py       |  <-- 主入口， orchestrator
|   (Evolution Loop)|
+--------+----------+
         |
    +----+----+ +---------+ +----------+ +---------+ +--------+ +----------+
    |mutator  | |hyperopt_| |evaluator | | safety  | |tracker | | prepare  |
    |.py      | |runner.py| |.py       | | .py     | | .py    | | .py      |
    |LLM变异  | |参数优化  | |回测评分  | | 4层门控 | |JSONL日志| | 基线建立 |
    +----+----+ +---------+ +----------+ +---------+ +--------+ +----------+
         |
    +----+----+
    |config.py|  <-- 配置中心（dataclass + JSON 覆盖）
    +---------+
```

## 3. 核心数据流

一次完整的进化迭代（Iteration）数据流：

```
Iteration N:

1. LLM 提出结构变异 (mutator.propose_mutation)
   输入: 当前最佳策略源码 + 历史实验记录 + baseline 指标
   输出: 变异后的策略源码

2. L1 静态检查 (safety.check_static)
   - AST 验证类结构完整
   - 危险代码检测（eval/exec/system）
   - 常见 LLM 错误检测（scalar 上调用 .shift()）

3. 写入策略文件 (mutator.apply_mutation)
   - 重命名类名匹配文件名
   - 复制父策略的 .json 参数文件

4. 智能空间检测 (mutator.detect_changed_spaces)
   - AST 对比 populate_entry_trend / populate_exit_trend / custom_stoploss
   - 未变化的空间从 hyperopt 中剔除，节省优化时间

5. Hyperopt 参数优化 IS (hyperopt_runner.run_hyperopt)
   - 在样本内时间范围运行 freqtrade hyperopt
   - 自动导出最优参数到 .json

6. IS 回测 (evaluator.evaluate_strategy)
   - 加载 hyperopt 后的参数运行回测
   - 提取关键指标

7. L2 硬限制检查 (safety.check_hard_limits)
   - 交易数 >= 30, 回撤 <= 15%, 盈利 > 0, PF > 1.0

8. OOS 回测 (evaluator.evaluate_strategy)
   - 使用相同参数在样本外时间范围运行

9. L3 OOS 验证 (safety.check_oos_validation)
   - OOS 交易数 >= 10, 盈利, PF > 1.0, Sharpe > 0
   - IS/OOS Sharpe 退化 < 5x（过拟合检测）

10. L3b Baseline-relative 门控 (safety.check_baseline_relative_validation)
    - OOS PnL/Sharpe/PF/DD/交易数必须达到 baseline 相对门槛

11. L4 Walk-forward 验证 (safety.check_walk_forward)
    - 将 IS+OOS 分成 N 段，至少 M 段盈利

12. 综合评分 (evaluator.compute_composite_score)
    - baseline-relative live score，baseline 约等于 1.0
    - score > best_score -> 保留改进
    - score <= best_score -> 丢弃退化
```

## 4. 关键设计决策

### 4.1 为什么用 AST 对比来检测变化空间？

在 `mutator.py` 的 `detect_changed_spaces()` 中，系统通过 AST dump 对比来检测 LLM 是否修改了特定方法：

- `populate_entry_trend` 变化 → 需要优化 `buy` 空间
- `populate_exit_trend` 变化 → 需要优化 `sell` 空间
- `custom_stoploss` 变化 → 需要优化 `stoploss` + `sell` 空间
- `populate_indicators` 变化 → 需要优化 `buy` + `sell`（因为指标可能影响两边）

**收益**：如果 LLM 只改了 exit 逻辑，不优化 buy 空间可以节省 20-30% 时间。

### 4.2 为什么有 Baseline-relative Gate？

`SafetyConfig` 中有 `min_oos_profit_vs_baseline` 等字段。这是为了防止 LLM 生成"看起来很漂亮但实际比 baseline 差"的策略：

- 只看 IS 容易过拟合
- 只看 OOS 可能选到交易太少、样本不稳健的策略
- 与 baseline 对比确保进化的方向是正确的

### 4.3 为什么用 JSONL 记录实验？

`tracker.py` 使用 append-only JSONL：

- 进程崩溃后不会丢失历史记录
- 可以 `tail -f` 实时查看
- 方便用 `jq` 做统计分析
- 加载时自动恢复 `_best_snapshot`

### 4.4 为什么 LLM 调用要每次新建 httpx.Client？

`mutator.py` 的 `_call_llm()` 中：

```python
with httpx.Client(timeout=httpx.Timeout(request_timeout)) as http_client:
    response = http_client.post(...)
```

**原因**：freqtrade backtest 内的 multiprocessing 会影响连接池状态。复用 httpx.Client 在若干次调用后会出现 "Connection error" 或 600 秒超时。每次新建并在 finally 中关闭是最安全的做法。

### 4.5 为什么 prompt 里要反复提醒 scalar method 错误？

LLM 训练数据中包含大量 pandas 代码，经常会在 `custom_exit()` 中写出：

```python
# 错误！current_candle 是 scalar
uptrend = current_candle["ema_50"] > current_candle["ema_50"].shift(10)
```

系统在三个层面防御：
1. **system prompt** (`program.md`)：明确禁止
2. **user prompt** (`_build_prompt`)：每次请求都加 REMINDER
3. **AST 静态检查** (`check_static`)：检测到就拒绝
4. **自动修复** (`_fix_scalar_methods`)：发现后自动删除错误调用

## 5. 文件职责矩阵

| 文件 | 职责 | 是否可变 | 关键类/函数 |
|------|------|--------|-----------|
| `evolve.py` | 主循环，orchestrator | 核心流程固定 | `run_evolution()` |
| `mutator.py` | LLM 调用 + 代码修复 + AST 检测 | 频繁修改优化 | `Mutator.propose_mutation()` |
| `hyperopt_runner.py` | 程序化 freqtrade hyperopt | 相对稳定 | `run_hyperopt()` |
| `evaluator.py` | 综合评分算法 | 评分权重可调 | `compute_composite_score()` |
| `safety.py` | 4 层安全门控 | 阈值可调 | `check_static()`, `check_walk_forward()` |
| `tracker.py` | JSONL 实验日志 + 最佳追踪 | 相对稳定 | `ExperimentTracker` |
| `prepare.py` | 基线建立 + 回测执行 | 相对稳定 | `establish_baseline()`, `run_backtest()` |
| `config.py` | 配置加载 + dataclass | 新增配置字段 | `load_evolution_config()` |
| `program.md` | LLM 系统指令 | 随策略调整 | 纯文本 prompt |
