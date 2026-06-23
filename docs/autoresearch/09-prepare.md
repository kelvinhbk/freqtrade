# prepare.py — 基线建立与回测执行深度解读

## 1. 文件职责

系统的"地基"，负责：
1. 加载 freqtrade 配置
2. 建立 baseline（如有需要先跑 hyperopt）
3. 执行单次回测
4. 从 freqtrade 的复杂 stats 字典中提取关键指标

## 2. `load_base_config`

```python
def load_base_config(config: EvolutionConfig) -> dict:
    args = {
        "config": [str(config_path)],
        "strategy": config.strategy_name,
        "strategy_path": config.strategy_output_dir,
        "timeframe": config.timeframe,
        "pairs": list(config.pairs),
        "timerange": config.in_sample_timerange,
        "export": "none",
        "runmode": RunMode.BACKTEST,
        "user_data_dir": str(Path(__file__).parent.parent.parent / "user_data"),
    }
    return setup_optimize_configuration(args, RunMode.BACKTEST)
```

**注意**：`setup_optimize_configuration` 同时用于 hyperopt 和 backtest，只是 `RunMode` 不同。这里用 `BACKTEST` 模式。

## 3. `run_backtest`

### 3.1 参数准备

```python
cfg = copy.deepcopy(base_config)
cfg["strategy"] = strategy_name
cfg["timerange"] = timerange_str
if "freqai" in cfg:
    cfg["freqai"]["identifier"] = f"autoresearch-{iteration:04d}"
```

**关键**：`copy.deepcopy` 确保每次回测不会修改共享的 `base_config`。`freqai.identifier` 隔离防止缓存冲突。

### 3.2 数据加载

```python
timerange = TimeRange.parse_timerange(timerange_str)
bt.config = cfg
bt.timerange = timerange
data, _ = bt.load_bt_data()
```

`bt` 是 `Backtesting` 实例，在 `establish_baseline()` 中创建并复用。`load_bt_data()` 只在数据未加载时实际读取磁盘。

### 3.3 策略加载与执行

```python
strategy = StrategyResolver.load_strategy(cfg)
bt.strategylist = [strategy]
bt.results = None           # 清空上次回测结果
bt.all_bt_content = {}      # 清空上次内容

min_date, max_date = bt.backtest_one_strategy(strategy, data, timerange)
```

**注意**：每次回测前必须清空 `results` 和 `all_bt_content`，否则 freqtrade 会累加或混淆结果。

### 3.4 Stats 生成

```python
stats = generate_backtest_stats(data, bt.all_bt_content, min_date=min_date, max_date=max_date)
strat_stats = stats["strategy"][strategy_name]
metrics = _extract_metrics(strat_stats, strategy_name)
```

`generate_backtest_stats` 是 freqtrade 的统计报告生成函数，返回一个包含所有策略统计的嵌套字典。

## 4. `_extract_metrics`

从 freqtrade 的 stats 字典中提取关键字段：

```python
def _extract_metrics(stats: dict, strategy_name: str) -> BacktestMetrics:
    total_trades = stats.get("total_trades", 0)
    trades: list[dict] = stats.get("trades", [])
    wins = sum(1 for t in trades if t.get("profit_abs", 0) > 0)

    return BacktestMetrics(
        strategy_name=strategy_name,
        total_trades=total_trades,
        profit_total_abs=stats.get("profit_total_abs", 0.0),
        profit_total_pct=stats.get("profit_total", 0.0) * 100,
        sharpe=stats.get("sharpe", 0.0),
        sortino=stats.get("sortino", 0.0),
        calmar=stats.get("calmar", 0.0),
        max_drawdown_account=stats.get("max_drawdown_account", 0.0),
        max_drawdown_abs=stats.get("max_drawdown_abs", 0.0),
        profit_factor=stats.get("profit_factor", 0.0),
        win_rate=wins / total_trades if total_trades > 0 else 0.0,
        avg_trade_duration_s=stats.get("holding_avg_s", 0.0),
        trades_per_day=stats.get("trades_per_day", 0.0),
    )
```

**关键字段映射**：

| freqtrade 字段 | BacktestMetrics 字段 | 说明 |
|---------------|---------------------|------|
| `profit_total_abs` | `profit_total_abs` | 绝对利润（ stake currency） |
| `profit_total` | `profit_total_pct` | 百分比利润，乘以 100 |
| `sharpe` | `sharpe` | 夏普比率 |
| `sortino` | `sortino` | 索提诺比率 |
| `calmar` | `calmar` | 卡玛比率 |
| `max_drawdown_account` | `max_drawdown_account` | 账户最大回撤（百分比） |
| `max_drawdown_abs` | `max_drawdown_abs` | 绝对回撤金额 |
| `profit_factor` | `profit_factor` | 盈亏比 |
| `holding_avg_s` | `avg_trade_duration_s` | 平均持仓时间（秒） |
| `trades_per_day` | `trades_per_day` | 日均交易数 |

**胜率计算**：从 `trades` 列表中逐个判断 `profit_abs > 0`，而不是用 freqtrade 提供的 `win_rate`。这是为了确保一致性（freqtrade 的 win_rate 可能包含不同的计算逻辑）。

## 5. `establish_baseline`

### 5.1 流程

```python
def establish_baseline(config, run_hyperopt=True):
    base_config = load_base_config(config)
    bt = Backtesting(base_config)
    
    params_file = Path(config.strategy_path).with_suffix(".json")
    if run_hyperopt and not params_file.exists():
        run_hyperopt(config, strategy_name, config.in_sample_timerange, list(config.hyperopt.spaces), 0)
    
    is_metrics, _ = run_backtest(bt, base_config, strategy_name, config.in_sample_timerange, 0)
    oos_metrics, _ = run_backtest(bt, base_config, strategy_name, config.out_of_sample_timerange, 0)
    return is_metrics, oos_metrics, base_config, bt
```

### 5.2 Baseline Hyperopt

如果基线策略没有 `.json` 参数文件，系统会先跑一轮 hyperopt：
- 使用完整的 `config.hyperopt.spaces`
- 在 IS 时间范围上优化
- 导出参数到策略同名的 `.json` 文件

**设计意图**：确保 baseline 是经过优化的，否则与后续候选策略的比较不公平（候选策略都会经过 hyperopt）。

### 5.3 Backtesting 实例复用

```python
bt = Backtesting(base_config)
# ...
return is_metrics, oos_metrics, base_config, bt
```

返回的 `bt` 实例在 `evolve.py` 中被保存并复用于所有后续回测。这是性能关键：避免每次迭代都重新加载历史数据。

## 6. 代码中的注意点

### 6.1 `copy.deepcopy`

```python
cfg = copy.deepcopy(base_config)
```

`base_config` 是一个大字典（包含嵌套配置），每次回测前深拷贝确保修改不会泄露。

### 6.2 `strategy_path` 的传递

```python
"strategy_path": config.strategy_output_dir,
```

这里传递的是**候选策略输出目录**，而不是原始策略目录。这是 freqtrade 加载策略的关键路径。
