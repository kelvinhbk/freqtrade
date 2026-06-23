# evaluator.py — 综合评分系统深度解读

## 1. 文件职责

负责运行回测并计算候选策略的综合评分。评分采用 **baseline-relative** 设计：baseline 约等于 1.0，超过 1.0 代表优于 baseline。

## 2. `evaluate_strategy`

这是一个薄包装函数：
```python
def evaluate_strategy(bt, base_config, strategy_name, timerange_str, iteration):
    return run_backtest(bt, base_config, strategy_name, timerange_str, iteration)
```

真正的回测逻辑在 `prepare.py` 的 `run_backtest()` 中。这里做包装是为了让 `evolve.py` 的调用语义更清晰。

## 3. `compute_composite_score`

### 3.1 评分哲学

不是用绝对阈值，而是用 **相对 baseline 的比例**：
- 候选 Sharpe = 2.0，baseline Sharpe = 1.0 → ratio_score = 2.0
- 候选 PnL = 50，baseline PnL = 100 → ratio_score = 0.5

这确保了评分具有**跨市场环境的可比性**。如果 baseline 本身就很差，候选不需要达到某个绝对标准也能得分。

### 3.2 辅助函数

```python
def clip(value, low=0.0, high=2.0):
    return max(low, min(high, value))

def ratio_score(value, reference, high=2.0):
    if reference > 0:
        return clip(value / reference, 0.0, high)
    if value > 0:
        return high
    if value == reference:
        return 1.0
    return 0.0
```

**边界情况处理**：
- reference > 0：正常比例
- reference <= 0 但 value > 0：给最高分（候选做到了 baseline 没做到的事）
- reference == value == 0：给 1.0（持平）
- reference <= 0 且 value <= 0：给 0.0（都失败了）

### 3.3 回撤评分

```python
def drawdown_score(metrics, reference):
    current_dd = metrics.max_drawdown_account
    reference_dd = reference.max_drawdown_account
    if current_dd <= 0:
        return 2.0 if reference_dd > 0 else 1.0
    if reference_dd <= 0:
        return 0.0
    return clip(reference_dd / current_dd)
```

**逻辑**：回撤越低越好，所以用 `reference_dd / current_dd`。如果候选回撤 5%，baseline 回撤 10%，得分 = 10/5 = 2.0。

### 3.4 交易数 cap

```python
"trade_count": ratio_score(metrics.total_trades, reference.total_trades, high=1.0)
```

**设计意图**：交易数的 `high=1.0` 意味着超过 baseline 不额外加分。防止系统鼓励过度交易。

### 3.5 胜率 cap

```python
"win_rate": ratio_score(metrics.win_rate, reference.win_rate, high=1.5)
```

胜率超过 baseline 的 1.5 倍后不再加分。这是一个相对宽松的上限。

### 3.6 权重归一化

```python
def normalized_metric_weights():
    raw_weights = (
        ("profit", weights.profit),
        ("sharpe", weights.sharpe),
        ...
    )
    positive_weights = tuple((name, max(0.0, weight)) for name, weight in raw_weights)
    total = sum(weight for _, weight in positive_weights)
    return tuple((name, weight / total) for name, weight in positive_weights if weight > 0)
```

**设计意图**：允许某些权重设为 0（如 `calmar=0.0`），系统会自动重新归一化剩余权重。这提供了灵活性，不需要用户手动确保权重和为 1。

### 3.7 单期评分

```python
def component_score(metrics, reference):
    metric_scores = {
        "profit": ratio_score(...),
        "sharpe": ratio_score(...),
        ...
    }
    return sum(weight * metric_scores[name] for name, weight in normalized_metric_weights())
```

### 3.8 IS/OOS 加权

```python
is_score = component_score(is_metrics, baseline_is)
oos_reference = baseline_oos or baseline_is
oos_score = component_score(oos_metrics, oos_reference)

is_weight = max(0.0, weights.in_sample)      # 默认 0.25
oos_weight = max(0.0, weights.out_of_sample)  # 默认 0.75
split_total = is_weight + oos_weight
score = (is_weight * is_score + oos_weight * oos_score) / split_total
```

**默认权重**：OOS 占 75%，IS 占 25%。这强烈倾向于选择在样本外表现好的策略，防止过拟合。

### 3.9 惩罚项

```python
if oos_metrics.profit_total_abs <= 0 or oos_metrics.profit_factor <= 1.0:
    score -= 0.50
```

**设计意图**：即使加权计算出的分数还不错，如果 OOS 不盈利或 PF <= 1.0，直接扣 0.5 分。这是一个硬性惩罚，确保不会保留"统计上还行但实际亏钱"的策略。

## 4. `score_to_str`

格式化输出评分信息：
```python
def score_to_str(score, is_m, oos_m):
    parts = [
        f"Score={score:.4f}",
        f"Sharpe={is_m.sharpe:.3f}",
        f"PF={is_m.profit_factor:.3f}",
        f"DD={is_m.max_drawdown_account:.1%}",
        f"Trades={is_m.total_trades}",
        f"PnL={is_m.profit_total_abs:.2f}",
    ]
    if oos_m:
        parts.append(f"OOS_Sharpe={oos_m.sharpe:.3f}")
        parts.append(f"OOS_PnL={oos_m.profit_total_abs:.2f}")
    return "  ".join(parts)
```
