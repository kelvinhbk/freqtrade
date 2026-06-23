# evolve.py — 主进化循环深度解读

## 1. 文件职责

AutoResearch 的入口文件和中央编排器（Orchestrator）。负责管理整个进化流程的生命周期：从基线建立到迭代循环，再到最终报告生成。

## 2. 入口设计

```python
if __name__ == "__main__":
    main()
```

CLI 参数设计：
```
--strategy           基础策略 .py 文件路径（必填）
--freqtrade-config   freqtrade 配置 .json 路径（必填）
--evolution-config   进化配置 .json 路径（可选，覆盖默认值）
--is-range           样本内时间范围
--oos-range          样本外时间范围
--max-iter           最大迭代次数
--max-hours          最大运行时间（小时）
```

**注意**：`build_config()` 中的逻辑允许命令行参数覆盖配置文件中的值，这是典型的 CLI 优先级设计。

## 3. 核心函数 `run_evolution()`

### 3.1 初始阶段

```python
is_baseline, oos_baseline, base_config, bt = establish_baseline(config)
```

`establish_baseline()` 来自 `prepare.py`，返回：
- IS 基线指标
- OOS 基线指标
- freqtrade 配置字典
- Backtesting 实例（后续复用，避免重复加载数据）

### 3.2 基线评分计算

```python
baseline_score = compute_composite_score(
    is_baseline, oos_baseline, is_baseline, config.score_weights, oos_baseline
)
```

这里 IS 和 OOS 的 reference 都是自己，所以 baseline score 理论值约等于 `1.0`。这是后续所有候选策略的参照系。

### 3.3 迭代循环结构

```python
for iteration in range(1, config.max_iterations + 1):
    # Walltime 检查
    elapsed = time.time() - start_time
    if elapsed > max_seconds:
        break
```

**设计决策**：walltime 检查放在循环开头，确保不会在一个迭代中间被中断。这意味着如果单次迭代（hyperopt + 回测）需要 1 小时，而 walltime 只剩 30 分钟，系统会在上次迭代结束后立即退出，不会启动新的 hyperopt。

### 3.4 Phase 1: LLM 变异

```python
mutation, mutated_code = mutator.propose_mutation(
    current_source, is_baseline, best, tracker.history()
)
```

输入：
- `current_source`: 当前最佳策略的源码（如果没有最佳，用原始基线）
- `is_baseline`: IS 基线指标（用于 prompt 中的 baseline metrics）
- `best`: 当前最佳快照（包含 generation、源码、指标）
- `tracker.history()`: 最近 20 条实验记录（用于 learning notes）

异常处理：
```python
except Exception as e:
    logger.error(f"Mutation failed: {e}")
    tracker.record_error(...)
    time.sleep(5)
    continue
```

**关键设计**：`time.sleep(5)` 是为了防止 API 限流或 transient error 导致疯狂消耗迭代次数。这是运维踩坑后的修复（见 ops-notes）。

### 3.5 L1: 静态检查

```python
ok, reason = check_static(mutated_code)
if not ok:
    tracker.record_rejection(mutation, None, reason, iteration)
    continue
```

静态检查不通过的策略会被记录为 `rejected`，但**不**执行 cleanup（因为还没写入文件）。

### 3.6 Phase 2: 应用变异

```python
strat_name, strat_path = mutator.apply_mutation(mutated_code, iteration, config.strategy_output_dir)
```

写入文件后，如果后续任何阶段失败，都会调用 `mutator.cleanup_strategy(strat_path)` 删除文件和参数。

### 3.7 Phase 3: 智能空间检测

```python
changed_spaces = mutator.detect_changed_spaces(current_source, mutated_code)
spaces = list(config.hyperopt.spaces)
if "buy" not in changed_spaces and "buy" in spaces:
    spaces.remove("buy")
```

**注意**：如果所有空间都被剔除了（比如 LLM 只改了注释），会回退到完整空间：
```python
if not spaces:
    spaces = list(config.hyperopt.spaces)
```

### 3.8 Phase 4: Hyperopt (IS)

```python
best_params = run_hyperopt(config, strat_name, config.in_sample_timerange, spaces, iteration)
```

Hyperopt 失败后的处理：
```python
except Exception as e:
    logger.error(f"Hyperopt failed: {e}")
    tracker.record_error(mutation, str(e), iteration)
    mutator.cleanup_strategy(strat_path)
    time.sleep(5)
    continue
```

### 3.9 Phase 5-6: IS 回测 + L2 硬限制

```python
is_metrics, _ = evaluate_strategy(bt, base_config, strat_name, config.in_sample_timerange, iteration)
ok, reason = check_hard_limits(is_metrics, config.safety)
```

### 3.10 Phase 7-8: OOS 回测 + L3 验证

```python
oos_metrics, _ = evaluate_strategy(bt, base_config, strat_name, config.out_of_sample_timerange, iteration)
ok, reason = check_oos_validation(oos_metrics, is_metrics, config.safety)
```

### 3.11 L3b: Baseline-relative Gate

```python
ok, reason = check_baseline_relative_validation(
    is_metrics, oos_metrics, is_baseline, oos_baseline, config.safety
)
```

这是比 L3 更严格的门控，确保候选策略不仅在绝对意义上合格，还要相对于 baseline 有竞争力。

### 3.12 L4: Walk-forward 验证

```python
validation_timerange = _combine_timeranges(config.in_sample_timerange, config.out_of_sample_timerange)
ok, reason = check_walk_forward(bt, base_config, strat_name, validation_timerange, config.safety, iteration)
```

`_combine_timeranges()` 将 IS 和 OOS 合并为一个完整的时间范围，然后切成 N 段做 walk-forward。

### 3.13 评分与决策

```python
score = compute_composite_score(is_metrics, oos_metrics, is_baseline, config.score_weights, oos_baseline)
best_score = tracker.current_best_score()

if score > best_score:
    logger.info(f"IMPROVEMENT! {best_score:.4f} -> {score:.4f}")
    tracker.record_improvement(...)
else:
    logger.info(f"Regression. Best={best_score:.4f} this={score:.4f}")
    tracker.record_regression(...)
    mutator.cleanup_strategy(strat_path)
```

## 4. 多进程启动方式

文件开头：
```python
try:
    multiprocessing.set_start_method("spawn")
except RuntimeError:
    pass
```

**原因**：macOS 上默认的 `fork` 启动方式在混合使用 multiprocessing 和第三方库（如 httpx、loky）时容易出问题。`spawn` 更慢但更干净，每个子进程都是全新 Python 解释器。

## 5. 代码中的注意点

### 5.1 异常处理模式

每个阶段都有 `try/except`，但处理模式略有不同：

- **Mutation / Hyperopt / Backtest 失败**：记录 error，cleanup，sleep(5)，continue
- **静态检查 / 硬限制 / OOS 验证失败**：记录 rejection/regression，cleanup，continue
- **Improvement**：不 cleanup，保留文件

### 5.2 Backtesting 实例复用

```python
bt = Backtesting(base_config)  # 在 establish_baseline 中创建
# 后续每次回测复用同一个 bt 实例
is_metrics, _ = evaluate_strategy(bt, base_config, ...)
oos_metrics, _ = evaluate_strategy(bt, base_config, ...)
```

这是性能优化：避免每次回测都重新加载历史数据和构建数据结构。

### 5.3 `nosec` 标记

```python
def run_evolution(config: EvolutionConfig) -> None:  # noqa: C901
```

`noqa: C901` 是忽略 mccabe 复杂度检查。这个函数确实很长（约 200 行），因为它串联了整个流程。拆分会降低可读性。
