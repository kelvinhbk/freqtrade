# hyperopt_runner.py — 程序化 Hyperopt 深度解读

## 1. 文件职责

将 freqtrade 的 CLI hyperopt 封装为可编程调用的 Python 函数。核心职责：
1. 构建 hyperopt 参数字典
2. 调用 freqtrade 内部 API 执行 hyperopt
3. 处理锁冲突和网络错误
4. 返回最优参数

## 2. `run_hyperopt` 函数

### 2.1 参数签名

```python
def run_hyperopt(
    config: EvolutionConfig,
    strategy_name: str,
    timerange: str,
    spaces: list[str] | None = None,
    iteration: int = 0,
) -> dict
```

返回值的结构：
```python
{
    'buy': {'buy_rsi': 30, 'buy_ema_period': 50},
    'sell': {'sell_rsi': 70},
    'roi': {'0': 0.05, '10': 0.02},
    'stoploss': {'stoploss': -0.05}
}
```

### 2.2 freqtrade 内部 API 调用

```python
from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.hyperopt import Hyperopt

args = _build_hyperopt_args(config, strategy_name, timerange, spaces_to_use)
hyperopt_config = setup_optimize_configuration(args, RunMode.HYPEROPT)
```

`setup_optimize_configuration` 是 freqtrade CLI 内部使用的配置构建函数。这里直接调用它，避免手动解析所有 freqtrade 配置选项。

### 2.3 自定义 Loss 注入

```python
hyperopt_config["custom_loss_weights"] = hc.custom_loss_weights
hyperopt_config["custom_loss_targets"] = hc.custom_loss_targets
```

**设计意图**：`AutoResearchMultiMetricLoss` 需要从配置中读取权重和目标值。通过注入到 hyperopt_config，自定义 loss 函数可以在运行时用 `self.config['custom_loss_weights']` 访问。

### 2.4 FreqAI 缓存隔离

```python
if "freqai" in hyperopt_config:
    hyperopt_config["freqai"]["identifier"] = f"autoresearch-hyperopt-{iteration:04d}"
```

**原因**：如果使用 FreqAI，不同迭代的模型缓存不能混用。用迭代编号作为唯一标识符，确保每次 hyperopt 使用独立的模型实例。

### 2.5 文件锁处理

```python
from filelock import FileLock, Timeout

lock = FileLock(Hyperopt.get_lock_filename(hyperopt_config))
with lock.acquire(timeout=1):
    hyperopt = Hyperopt(hyperopt_config)
    hyperopt.start()
```

**设计意图**：freqtrade hyperopt 使用文件锁防止并发运行。如果锁被占用（如上次异常退出未释放），`timeout=1` 秒后会抛出 `Timeout` 异常。

在 `evolve.py` 中，这个异常会向上传播，导致当前迭代标记为 error。用户需要手动删除 `user_data/hyperopt.lock` 来恢复。

### 2.6 网络错误重试

```python
except Exception as e:
    err_msg = str(e)
    is_network_error = any(
        keyword in err_msg.lower()
        for keyword in ["exchange", "cannot connect", "connection", "timeout", "unavailable"]
    ) or "load markets" in err_msg.lower()
    if is_network_error and attempt < max_retries:
        wait_sec = 30 * attempt
        time.sleep(wait_sec)
        continue
```

**重试策略**：网络错误等待 30s、60s、90s 后重试。这是因为在长任务运行期间，交易所 API 可能出现临时不可用。

### 2.7 日志级别调整

```python
logging.getLogger("hyperopt.tpe").setLevel(logging.WARNING)
logging.getLogger("filelock").setLevel(logging.WARNING)
```

**原因**：TPE（Tree-structured Parzen Estimator）的日志非常冗长，在自动运行中会淹没有用信息。

## 3. `_build_hyperopt_args`

构建传给 `setup_optimize_configuration` 的参数字典：

```python
args = {
    "config": [str(Path(config.base_config_path))],
    "strategy": strategy_name,
    "strategy_path": config.strategy_output_dir,
    "timeframe": config.timeframe,
    "pairs": list(config.pairs),
    "timerange": timerange,
    "epochs": hc.epochs,
    "spaces": spaces_to_use,
    "hyperopt_loss": hc.loss_function,
    "hyperopt_jobs": hc.jobs,
    "hyperopt_min_trades": hc.min_trades,
    "user_data_dir": str(Path(__file__).parent.parent.parent / "user_data"),
}
```

**关键字段**：
- `strategy_path`: 必须指向候选策略的输出目录，否则 freqtrade 找不到策略类
- `hyperopt_jobs`: -1 表示使用全部 CPU，在配置中通过 `jobs` 控制
- `user_data_dir`: 硬编码为项目根目录下的 `user_data/`，确保数据文件和锁文件位置正确

## 4. `load_hyperopt_params`

```python
def load_hyperopt_params(strategy_name: str, strategy_dir: str) -> dict:
    params_file = Path(strategy_dir) / f"{strategy_name}.json"
    if not params_file.exists():
        return {}
    data = json.loads(params_file.read_text())
    return data.get("params", {})
```

这个函数目前似乎没有被主流程直接调用（参数文件由 freqtrade hyperopt 自动导出并加载），但提供了程序化读取参数的接口。

## 5. 代码中的注意点

### 5.1 延迟导入

```python
from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.optimize.hyperopt import Hyperopt
```

这些导入在函数内部执行，而不是模块顶部。这是为了避免在导入 `hyperopt_runner.py` 时就触发 freqtrade 的复杂初始化逻辑。

### 5.2 FileLock 的延迟导入

```python
from filelock import FileLock, Timeout
```

同样在函数内部导入，因为 `filelock` 是可选依赖（如果未安装，会抛出 ImportError）。
