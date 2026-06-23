# tracker.py — 实验追踪系统深度解读

## 1. 文件职责

Append-only JSONL 实验日志，负责：
1. 记录每次迭代的完整实验数据
2. 追踪当前最佳策略（内存 + 持久化）
3. 生成进化报告
4. 支持进程崩溃后恢复状态

## 2. 数据模型

### 2.1 BacktestMetrics

```python
@dataclass
class BacktestMetrics:
    strategy_name: str
    total_trades: int
    profit_total_abs: float
    profit_total_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_account: float
    max_drawdown_abs: float
    profit_factor: float
    win_rate: float
    avg_trade_duration_s: float
    trades_per_day: float
```

**注意**：`profit_total_pct` 从 freqtrade 的 `profit_total` 乘以 100 得到。freqtrade 内部存储的是小数形式（如 0.05 表示 5%）。

### 2.2 MutationRecord

```python
@dataclass
class MutationRecord:
    mutation_type: str          # 如 "llm_driven"
    description: str            # 简短描述（前 200 字符）
    llm_analysis: str = ""      # LLM 的 ANALYSIS 部分
    llm_expected_impact: str = ""  # LLM 的 EXPECTED_IMPACT 部分
```

### 2.3 ExperimentRecord

```python
@dataclass
class ExperimentRecord:
    experiment_id: str          # 如 "ar-0001-a3f7"
    iteration: int
    timestamp: str              # ISO 8601 格式
    generation: int             # 当前最佳策略的代数
    mutation: MutationRecord
    in_sample: BacktestMetrics | None
    out_of_sample: BacktestMetrics | None
    composite_score: float
    status: str                 # baseline/improvement/regression/rejected/error
    rejection_reason: str | None
```

**generation 的含义**：
- generation=0：baseline 策略
- generation=N：第 N 次改进后的策略（每次 improvement 会递增）

### 2.4 StrategySnapshot

```python
@dataclass
class StrategySnapshot:
    strategy_name: str
    source_path: Path
    source_code: str            # 完整源码
    composite_score: float
    in_sample: BacktestMetrics
    out_of_sample: BacktestMetrics
    generation: int
```

## 3. ExperimentTracker

### 3.1 初始化与恢复

```python
def __init__(self, config: EvolutionConfig):
    self.results_path = config.results_path
    self._records: list[ExperimentRecord] = []
    self._best_snapshot: StrategySnapshot | None = None
    self._load_existing()
```

`_load_existing()` 在初始化时读取已有的 JSONL 文件：
- 逐行解析
- 如果某行解析失败（如被其他进程写坏），跳过该行
- 对于 `status == "improvement"` 的记录，恢复 `_best_snapshot`

### 3.2 持久化机制

```python
def _append(self, record: ExperimentRecord) -> None:
    self.results_path.parent.mkdir(parents=True, exist_ok=True)
    with self.results_path.open("a") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    self._records.append(record)
```

**Append-only 设计**：
- 每次只追加一行，不修改历史
- 即使进程在写入时崩溃，最多丢失当前这行
- 支持多个进程（不同时）安全追加

### 3.3 状态记录方法

| 方法 | 调用时机 | status |
|------|---------|--------|
| `set_baseline` | 进化开始前 | `baseline` |
| `record_improvement` | score > best_score | `improvement` |
| `record_regression` | score <= best_score | `regression` |
| `record_rejection` | 静态检查/硬限制失败 | `rejected` |
| `record_error` | 异常 | `error` |

### 3.4 Best Snapshot 管理

```python
@property
def _generation(self) -> int:
    return self._best_snapshot.generation if self._best_snapshot else 0

def current_best(self) -> StrategySnapshot | None:
    return self._best_snapshot

def current_best_score(self) -> float:
    return self._best_snapshot.composite_score if self._best_snapshot else 0.0
```

`current_best_score()` 返回 0.0 如果没有 best snapshot。这意味着第一次候选策略的 score 只要 > 0 就会被认为是 improvement。实际上第一次评分通常 > 0（除非 OOS 很差被扣分）。

### 3.5 报告生成

```python
def generate_report(self) -> str:
    improvements = sum(1 for r in self._records if r.status == "improvement")
    regressions = sum(1 for r in self._records if r.status == "regression")
    rejections = sum(1 for r in self._records if r.status == "rejected")
    errors = sum(1 for r in self._records if r.status == "error")
```

报告包含：
- 总迭代数
- 各状态统计
- 最佳策略名称、代数、评分
- IS 和 OOS 的关键指标

## 4. 代码中的注意点

### 4.1 `_make_id`

```python
def _make_id(self, iteration: int) -> str:
    return f"ar-{iteration:04d}-{uuid.uuid4().hex[:4]}"
```

使用 UUID 后 4 位作为随机后缀，确保同一迭代如果重试不会 ID 冲突。

### 4.2 `_update_best_from_record`

```python
def _update_best_from_record(self, record: ExperimentRecord) -> None:
    name = record.in_sample.strategy_name
    path = Path(self.config.strategy_output_dir) / f"{name}.py"
    code = path.read_text() if path.exists() else ""
```

**注意**：恢复时从磁盘读取源码文件。如果文件已被删除（如手动清理），`code` 会是空字符串，但不影响评分比较。

### 4.3 `to_dict` 的递归处理

```python
def to_dict(self) -> dict[str, Any]:
    d = {
        "experiment_id": self.experiment_id,
        ...
    }
    d["in_sample"] = self.in_sample.to_dict() if self.in_sample else None
    d["out_of_sample"] = self.out_of_sample.to_dict() if self.out_of_sample else None
    return d
```

嵌套 dataclass 的手动序列化。没有用 `asdict` 递归是因为需要控制字段名和空值处理。
