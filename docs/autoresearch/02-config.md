# config.py — 配置系统深度解读

## 1. 文件职责

配置中心，负责从 JSON 配置文件加载所有进化参数。使用 Python `dataclasses`（frozen）实现不可变配置对象，避免运行中意外修改。

## 2. 类结构

### 2.1 LLMProviderConfig

```python
@dataclass(frozen=True)
class LLMProviderConfig:
    name: str
    base_url: str
    api_key_env: str          # .env 中的环境变量名
    model: str
    enabled: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    request_timeout: int | None = None
    max_retries: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
```

**关键设计**：每个 provider 可以覆盖全局默认值。例如全局 `temperature=0.7`，但某个 provider 可以单独设为 `0.5`。

`effective_*` 方法实现级联读取：
```python
def effective_temperature(self, defaults: "LLMConfig") -> float:
    return self.temperature if self.temperature is not None else defaults.temperature
```

**API Key 读取**：
```python
def get_api_key(self) -> str:
    if dotenv is not None:
        dotenv.load_dotenv(BASE_DIR / ".env")
    key = os.environ.get(self.api_key_env, "")
```

注意：`dotenv.load_dotenv()` 每次调用都会重新加载 .env 文件，这在多 provider fallback 时确保即使 .env 被外部修改也能读到最新值。

### 2.2 LLMConfig

```python
@dataclass(frozen=True)
class LLMConfig:
    default_provider: str = "kimi"
    fallback_order: tuple[str, ...] = ("kimi", "glm", "deepseek")
    providers: tuple[LLMProviderConfig, ...] = field(default_factory=tuple)
```

**ordered_providers()** 方法：返回按优先级排序的可用 provider 列表。

逻辑：
1. 将 `default_provider` 放在首位
2. 按 `fallback_order` 顺序追加
3. 最后追加剩余的 enabled provider

这确保了即使配置文件中 provider 顺序混乱，实际调用顺序也是确定的。

**向后兼容**：`_legacy_provider()` 方法处理旧版单 provider 配置（直接写在 `llm` 下的 `base_url`/`api_key_env`/`model`）。

### 2.3 SafetyConfig

```python
@dataclass(frozen=True)
class SafetyConfig:
    max_drawdown_pct: float = 0.20
    min_trade_count: int = 30
    min_oos_trade_count: int = 10
    max_oos_drawdown_pct: float = 0.25
    require_oos_positive: bool = True
    max_sharpe_degradation: float = 5.0
    walk_forward_splits: int = 3
    min_walk_forward_profitable: int = 2
    min_walk_forward_trades: int = 10
    # Baseline-relative gates:
    min_oos_profit_vs_baseline: float = 0.0
    min_oos_sharpe_vs_baseline: float = 0.0
    ...
```

**注意**：所有 `min_*_vs_baseline` 和 `max_*_vs_baseline` 默认值为 `0.0`，表示不启用。在正式配置中通过 JSON 覆盖来启用。

### 2.4 ScoreWeights

```python
@dataclass(frozen=True)
class ScoreWeights:
    profit: float = 0.30
    sharpe: float = 0.25
    profit_factor: float = 0.15
    drawdown: float = 0.15
    trade_count: float = 0.10
    win_rate: float = 0.05
    calmar: float = 0.0
    in_sample: float = 0.25
    out_of_sample: float = 0.75
```

`trade_count` 权重存在但会在评分时被 cap（不超过 baseline 不额外加分），防止过度交易。

### 2.5 HyperoptConfig

```python
@dataclass(frozen=True)
class HyperoptConfig:
    loss_function: str = "SharpeHyperOptLoss"
    epochs: int = 500
    spaces: tuple[str, ...] = ("buy", "sell", "roi", "stoploss")
    jobs: int = -1              # -1 = 使用全部 CPU
    random_state: int | None = None
    min_trades: int = 30
    early_stop: int = 0
    analyze_per_epoch: bool = False
    fee: float | None = None
    custom_loss_weights: dict[str, float] = ...
    custom_loss_targets: dict[str, float] = ...
```

`custom_loss_*` 用于 `AutoResearchMultiMetricLoss`，在 `hyperopt_runner.py` 中被注入到 hyperopt 配置中。

### 2.6 EvolutionConfig

顶级配置容器，包含以上所有子配置。

**关键方法 `__post_init__`**：
```python
def __post_init__(self) -> None:
    def _resolve(path: str) -> str:
        if not path:
            return path
        p = Path(path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return str(p.resolve())
```

这确保了无论脚本从哪个目录运行，相对路径都能正确解析到项目根目录。

## 3. 配置加载流程

```
load_evolution_config(config_path)
  ├── 读取 JSON
  ├── _parse_llm_config()          # 解析 LLM 配置（多 provider / 单 provider）
  ├── 解析 hyperopt spaces         # string/list 统一转为 tuple
  ├── 解析 mutation_constraints    # string 转为 tuple
  └── 组装 EvolutionConfig
```

## 4. 代码中的注意点

### 4.1 frozen dataclass 的修改

因为使用了 `frozen=True`，`__post_init__` 中必须用 `object.__setattr__`：
```python
object.__setattr__(self, "strategy_path", _resolve(self.strategy_path))
```

### 4.2 mutation_constraints 的类型处理

```python
mutation_constraints = data.get("mutation_constraints", ())
if isinstance(mutation_constraints, str):
    mutation_constraints = (mutation_constraints,)
```

这允许 JSON 中既写 `"mutation_constraints": "exit-only"` 也写 `"mutation_constraints": ["exit-only", "no-trailing"]`。

### 4.3 fallback_order 的字符串解析

```python
fallback_order = llm_data.get("fallback_order", ("kimi", "glm", "deepseek"))
if isinstance(fallback_order, str):
    fallback_order = tuple(part.strip() for part in fallback_order.split(",") if part.strip())
```

支持 JSON 中写 `"fallback_order": "kimi, glm, deepseek"` 或列表形式。

## 5. 默认配置

`_default_llm_config()` 硬编码了三个 provider：

| Provider | Base URL | Model |
|----------|----------|-------|
| kimi | `https://api.kimi.com/coding` | `kimi-2.6` |
| glm | `https://open.bigmodel.cn/api/anthropic` | `glm-5.1` |
| deepseek | `https://api.deepseek.com/anthropic` | `deepseek-v4-pro` |

所有 provider 都走 Anthropic-compatible `/v1/messages` 协议。
