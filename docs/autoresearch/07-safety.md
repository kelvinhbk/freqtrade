# safety.py — 四层安全门控深度解读

## 1. 文件职责

安全是 AutoResearch 的核心。四层门控确保只有高质量、低风险、非过拟合的策略才能被保留。

| 层级 | 名称 | 检查时机 | 成本 |
|------|------|---------|------|
| L1 | Static Check | 写入文件前 | 极低（毫秒级） |
| L2 | Hard Limits | IS 回测后 | 低 |
| L3 | OOS Validation | OOS 回测后 | 中 |
| L3b | Baseline-relative | OOS 回测后 | 中 |
| L4 | Walk-forward | 最终候选 | 高（额外多次回测） |

## 2. L1: `check_static` — AST 静态分析

### 2.1 语法检查

```python
try:
    tree = ast.parse(source_code)
except SyntaxError as e:
    return False, f"Syntax error: {e}"
```

### 2.2 类结构检查

```python
required = {"populate_indicators", "populate_entry_trend", "populate_exit_trend"}
found = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
missing = required - found
```

确保策略类包含 freqtrade 要求的三个核心方法。

### 2.3 危险代码检测

```python
if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "call", "run"}:
    return False, f"Dangerous attribute: {node.attr}"

if isinstance(func, ast.Name) and func.id in {"eval", "exec", "compile"}:
    return False, f"Dangerous call: {func.id}"
```

**注意**：这里检测的是 `ast.Attribute`（如 `os.system`）和 `ast.Name`（如 `eval()`）。但有一个边界情况：如果代码写 `import os; getattr(os, "system")(...)`，这种反射式调用无法被 AST 静态检测到。

### 2.4 Scalar Method 错误检测

```python
banned = {"shift", "rolling", "diff", "pct_change"}
for stmt in body:
    for child in ast.walk(stmt):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr in banned:
                if isinstance(func.value, ast.Subscript):
                    return False, f"Invalid call: scalar value cannot use .{func.attr}()..."
```

**检测逻辑**：在 `custom_exit` 和 `custom_stoploss` 方法体内，如果看到 `Subscript`（如 `current_candle["ema_50"]`）后面跟着 `.shift()` 等调用，就拒绝。

### 2.5 Series 布尔条件检测

```python
def _find_dataframe_in_conditions(method_node):
    # 收集所有属于 Call 链的 Subscript（如 dataframe['close'].iloc[-1] 是 OK 的）
    subscripts_in_calls: set[int] = set()
    for node in ast.walk(method_node):
        if isinstance(node, ast.Call):
            func = node.func
            while isinstance(func, ast.Attribute):
                func = func.value
            if isinstance(func, ast.Subscript) and func.value.id == "dataframe":
                subscripts_in_calls.add(id(func))
    
    # 检查 If/While 条件中的 Subscript
    for node in ast.walk(method_node):
        if isinstance(node, (ast.If, ast.While)):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Subscript) and id(sub) not in subscripts_in_calls:
                    if sub.value.id == "dataframe":
                        errors.append("dataframe[...] used directly in boolean condition")
```

**逻辑**：`dataframe['col']` 是 pandas Series，不能直接用于 `if` 条件。但 `dataframe['col'].iloc[-1]` 是 scalar，可以用于条件。系统通过判断 Subscript 是否在 Call 链中来区分这两种情况。

### 2.6 `trade.is_long` 检测

```python
if re.search(r"\btrade\.is_long\b", source_code):
    return False, "Invalid attribute: trade.is_long does not exist. Use not trade.is_short instead."
```

## 3. L2: `check_hard_limits` — IS 硬限制

```python
checks = [
    (metrics.total_trades >= config.min_trade_count, f"Trades {metrics.total_trades} < {config.min_trade_count}"),
    (metrics.max_drawdown_account <= config.max_drawdown_pct, f"DD {metrics.max_drawdown_account:.1%} > {config.max_drawdown_pct:.1%}"),
    (metrics.profit_total_abs > 0, f"Unprofitable: {metrics.profit_total_abs:.2f}"),
    (metrics.profit_factor > 1.0, f"PF {metrics.profit_factor:.2f} <= 1.0"),
]
```

简单直接的阈值检查，任何一个不满足就拒绝。

## 4. L3: `check_oos_validation` — OOS 验证

```python
checks = [
    (oos.total_trades >= config.min_oos_trade_count, ...),
    (oos.max_drawdown_account <= config.max_oos_drawdown_pct, ...),
    (oos.profit_total_abs > 0, ...),  # 如果 require_oos_positive
    (oos.profit_factor > 1.0, ...),
    (oos.sharpe > 0, ...),
]
```

### 4.1 过拟合检测

```python
if is_m.sharpe > 0:
    degradation = is_m.sharpe / max(oos.sharpe, 0.01)
    checks.append((degradation < config.max_sharpe_degradation, f"Overfitting: IS/OOS sharpe = {degradation:.1f}x"))
```

**阈值**：默认 `max_sharpe_degradation=5.0`。如果 IS Sharpe 是 5.0，OOS Sharpe 必须 >= 1.0。这是一个相对宽松的过拟合检查。

## 5. L3b: `check_baseline_relative_validation`

这是比 L3 更严格的门控，所有阈值都是相对于 baseline 的比例：

```python
def _ratio(value, reference):
    if reference == 0:
        return float("inf") if value > 0 else 0.0
    return value / reference
```

检查项（仅在配置中启用时）：
- `min_oos_profit_vs_baseline`: OOS PnL / baseline OOS PnL >= 阈值
- `min_oos_sharpe_vs_baseline`: OOS Sharpe / baseline OOS Sharpe >= 阈值
- `min_oos_profit_factor_vs_baseline`: OOS PF / baseline OOS PF >= 阈值
- `max_oos_drawdown_vs_baseline`: OOS DD / baseline OOS DD <= 阈值（回撤是越低越好）
- `min_oos_trades_vs_baseline`: OOS Trades / baseline OOS Trades >= 阈值
- `min_is_profit_vs_baseline`: IS PnL / baseline IS PnL >= 阈值
- `min_is_sharpe_vs_baseline`: IS Sharpe / baseline IS Sharpe >= 阈值

**ENEW 正式配置示例**：
- OOS PnL >= 1.00x baseline
- OOS Sharpe >= 0.85x baseline
- OOS PF >= 1.00x baseline
- OOS DD <= 1.20x baseline
- OOS Trades >= 0.40x baseline
- IS PnL >= 0.60x baseline
- IS Sharpe >= 0.60x baseline

## 6. L4: `check_walk_forward` — 滚动验证

### 6.1 时间窗口切分

```python
splits = max(config.walk_forward_splits, 1)
fold_len = (end - start) / splits
for i in range(splits):
    fold_start = start + i * fold_len
    fold_end = end if i == splits - 1 else start + (i + 1) * fold_len
```

将 IS + OOS 的完整时间范围切成 N 段，每段独立回测。

### 6.2 通过标准

```python
passed = (
    metrics.total_trades >= config.min_walk_forward_trades
    and metrics.profit_total_abs > 0
    and metrics.profit_factor > 1.0
    and metrics.max_drawdown_account <= config.max_oos_drawdown_pct
)
```

默认配置：分成 3 段，至少 2 段通过。这验证了策略在不同时间段的稳健性。

### 6.3 性能考量

Walk-forward 是最昂贵的检查，因为它需要额外运行 `splits` 次回测。但它只在通过了 L1-L3b 的候选策略上执行，所以实际开销可控。
