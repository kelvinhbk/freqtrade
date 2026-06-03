"""
AutoResearch for Freqtrade - Evolution Configuration
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


try:
    import dotenv
except ImportError:
    dotenv = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent.parent


@dataclass(frozen=True)
class LLMProviderConfig:
    """One Anthropic-compatible LLM provider endpoint."""

    name: str
    base_url: str
    api_key_env: str
    model: str
    enabled: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    request_timeout: int | None = None
    max_retries: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    max_context_tokens: int | None = None

    def get_api_key(self) -> str:
        if dotenv is not None:
            dotenv.load_dotenv(BASE_DIR / ".env")
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ValueError(
                f"API key not found for provider '{self.name}': set {self.api_key_env} in .env"
            )
        return key

    def effective_temperature(self, defaults: "LLMConfig") -> float:
        return self.temperature if self.temperature is not None else defaults.temperature

    def effective_max_tokens(self, defaults: "LLMConfig") -> int:
        return self.max_tokens if self.max_tokens is not None else defaults.max_tokens

    def effective_request_timeout(self, defaults: "LLMConfig") -> int:
        return (
            self.request_timeout
            if self.request_timeout is not None
            else defaults.request_timeout
        )

    def effective_max_retries(self, defaults: "LLMConfig") -> int:
        return self.max_retries if self.max_retries is not None else defaults.max_retries

    def effective_max_context_tokens(self, defaults: "LLMConfig") -> int:
        return self.max_context_tokens if self.max_context_tokens is not None else 200000


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    request_timeout: int = 120
    max_retries: int = 3
    default_provider: str = "kimi"
    fallback_order: tuple[str, ...] = ("kimi", "glm", "deepseek")
    providers: tuple[LLMProviderConfig, ...] = field(default_factory=tuple)

    def get_api_key(self) -> str:
        if dotenv is not None:
            dotenv.load_dotenv(BASE_DIR / ".env")
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ValueError(f"API key not found: set {self.api_key_env} in .env")
        return key

    def ordered_providers(self) -> tuple[LLMProviderConfig, ...]:
        """Return enabled providers ordered by default + fallback priority."""
        providers = self.providers or self._legacy_provider()
        provider_map = {provider.name: provider for provider in providers if provider.enabled}

        order: list[str] = []
        for name in (self.default_provider, *self.fallback_order):
            if name and name not in order:
                order.append(name)

        ordered = [provider_map[name] for name in order if name in provider_map]
        remaining = [
            provider
            for provider in providers
            if provider.enabled and provider.name not in {p.name for p in ordered}
        ]
        return tuple(ordered + remaining)

    def _legacy_provider(self) -> tuple[LLMProviderConfig, ...]:
        if not (self.base_url and self.api_key_env and self.model):
            return ()
        return (
            LLMProviderConfig(
                name=self.default_provider or "default",
                base_url=self.base_url,
                api_key_env=self.api_key_env,
                model=self.model,
            ),
        )


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
    min_oos_profit_vs_baseline: float = 0.0
    min_oos_sharpe_vs_baseline: float = 0.0
    min_oos_profit_factor_vs_baseline: float = 0.0
    max_oos_drawdown_vs_baseline: float = 0.0
    min_oos_trades_vs_baseline: float = 0.0
    min_is_profit_vs_baseline: float = 0.0
    min_is_sharpe_vs_baseline: float = 0.0


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


@dataclass(frozen=True)
class HyperoptConfig:
    """Hyperopt parameter optimization settings."""

    loss_function: str = "SharpeHyperOptLoss"
    epochs: int = 500
    spaces: tuple[str, ...] = ("buy", "sell", "roi", "stoploss")
    jobs: int = -1
    random_state: int | None = None
    min_trades: int = 30
    early_stop: int = 0
    analyze_per_epoch: bool = False
    fee: float | None = None
    custom_loss_weights: dict[str, float] = field(
        default_factory=lambda: {
            "sharpe": 0.30,
            "profit_factor": 0.25,
            "calmar": 0.20,
            "win_rate": 0.10,
            "trade_density": 0.15,
        }
    )
    custom_loss_targets: dict[str, float] = field(
        default_factory=lambda: {
            "sharpe": 2.0,
            "profit_factor": 1.5,
            "calmar": 2.0,
            "win_rate": 0.55,
            "trade_density": 1.0,
        }
    )


@dataclass(frozen=True)
class EvolutionConfig:
    llm: LLMConfig
    max_iterations: int = 500
    max_walltime_hours: float = 24.0
    in_sample_timerange: str = "20240701-20250101"
    out_of_sample_timerange: str = "20250101-20250501"
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    score_weights: ScoreWeights = field(default_factory=ScoreWeights)
    hyperopt: HyperoptConfig = field(default_factory=HyperoptConfig)
    strategy_path: str = ""
    base_config_path: str = ""
    pairs: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT")
    timeframe: str = "15m"
    results_file: str = ""
    strategy_output_dir: str = ""
    mutation_focus: str = ""
    mutation_constraints: tuple[str, ...] = field(default_factory=tuple)
    # Three-phase evolution mode
    evolution_mode: str = "classic"
    param_convergence_rounds: int = 3
    param_improvement_threshold: float = 0.05
    stagnation_limit: int = 10
    max_restarts: int = 3
    # Database and storage paths
    db_path: str = ""
    blacklist_dir: str = ""
    # Dynamic epochs
    epochs_floor: int = 200
    epochs_ceiling: int = 1000
    # Cross-strategy blacklist sharing
    global_blacklist_threshold: int = 3

    def __post_init__(self) -> None:
        # Resolve relative paths against PROJECT_ROOT so configs work from any CWD
        def _resolve(path: str) -> str:
            if not path:
                return path
            p = Path(path)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            return str(p.resolve())

        object.__setattr__(self, "strategy_path", _resolve(self.strategy_path))
        object.__setattr__(self, "base_config_path", _resolve(self.base_config_path))
        object.__setattr__(
            self,
            "results_file",
            _resolve(self.results_file) or str(BASE_DIR / "results" / "experiments.jsonl"),
        )
        object.__setattr__(
            self,
            "strategy_output_dir",
            _resolve(self.strategy_output_dir)
            or str(PROJECT_ROOT / "user_data" / "strategies" / "autoresearch"),
        )
        object.__setattr__(
            self,
            "db_path",
            _resolve(self.db_path) or str(PROJECT_ROOT / "user_data" / "dbs"),
        )
        object.__setattr__(
            self,
            "blacklist_dir",
            _resolve(self.blacklist_dir) or str(BASE_DIR / "blacklists"),
        )

    @property
    def strategy_name(self) -> str:
        return Path(self.strategy_path).stem

    @property
    def results_path(self) -> Path:
        return Path(self.results_file)

    @property
    def db_path_resolved(self) -> Path:
        return Path(self.db_path)

    @property
    def blacklist_path(self) -> Path:
        return Path(self.blacklist_dir)

    @property
    def strategy_blacklist_file(self) -> Path:
        return Path(self.blacklist_dir) / f"{self.strategy_name}.json"

    @property
    def global_blacklist_file(self) -> Path:
        return Path(self.blacklist_dir) / "global.json"


def load_evolution_config(config_path: Path | str | None = None) -> EvolutionConfig:
    """Load evolution config from JSON file, or use defaults."""
    if config_path is None:
        config_path = BASE_DIR / "evolution_config.json"

    config_path = Path(config_path)
    if not config_path.exists():
        return _default_config()

    with config_path.open() as f:
        data = json.load(f)

    llm_data = data.get("llm")
    safety_data = data.get("safety", {})
    weights_data = data.get("score_weights", {})
    hyperopt_data = data.get("hyperopt", {})
    mutation_constraints = data.get("mutation_constraints", ())
    if isinstance(mutation_constraints, str):
        mutation_constraints = (mutation_constraints,)
    else:
        mutation_constraints = tuple(mutation_constraints)

    # Parse hyperopt spaces as tuple
    spaces = hyperopt_data.get("spaces", ["buy", "sell", "roi", "stoploss"])
    if isinstance(spaces, str):
        spaces = spaces.split()

    hyperopt_cfg = HyperoptConfig(
        loss_function=hyperopt_data.get("loss_function", "SharpeHyperOptLoss"),
        epochs=hyperopt_data.get("epochs", 500),
        spaces=tuple(spaces),
        jobs=hyperopt_data.get("jobs", -1),
        random_state=hyperopt_data.get("random_state"),
        min_trades=hyperopt_data.get("min_trades", 30),
        early_stop=hyperopt_data.get("early_stop", 0),
        analyze_per_epoch=hyperopt_data.get("analyze_per_epoch", False),
        fee=hyperopt_data.get("fee"),
        custom_loss_weights=hyperopt_data.get(
            "custom_loss_weights", HyperoptConfig().custom_loss_weights
        ),
        custom_loss_targets=hyperopt_data.get(
            "custom_loss_targets", HyperoptConfig().custom_loss_targets
        ),
    )

    llm_cfg = _parse_llm_config(llm_data)

    return EvolutionConfig(
        llm=llm_cfg,
        max_iterations=data.get("max_iterations", 500),
        max_walltime_hours=data.get("max_walltime_hours", 24.0),
        in_sample_timerange=data.get("in_sample_timerange", "20240701-20250101"),
        out_of_sample_timerange=data.get("out_of_sample_timerange", "20250101-20250501"),
        safety=SafetyConfig(**safety_data),
        score_weights=ScoreWeights(**weights_data),
        hyperopt=hyperopt_cfg,
        strategy_path=data.get("strategy_path", ""),
        base_config_path=data.get("base_config_path", ""),
        pairs=tuple(data.get("pairs", ("BTC/USDT:USDT", "ETH/USDT:USDT"))),
        timeframe=data.get("timeframe", "15m"),
        results_file=data.get("results_file", ""),
        strategy_output_dir=data.get("strategy_output_dir", ""),
        mutation_focus=data.get("mutation_focus", ""),
        mutation_constraints=mutation_constraints,
        evolution_mode=data.get("evolution_mode", "classic"),
        param_convergence_rounds=data.get("param_convergence_rounds", 3),
        param_improvement_threshold=data.get("param_improvement_threshold", 0.05),
        stagnation_limit=data.get("stagnation_limit", 10),
        max_restarts=data.get("max_restarts", 3),
        db_path=data.get("db_path", ""),
        blacklist_dir=data.get("blacklist_dir", ""),
        epochs_floor=data.get("epochs_floor", 200),
        epochs_ceiling=data.get("epochs_ceiling", 1000),
        global_blacklist_threshold=data.get("global_blacklist_threshold", 3),
    )


def _parse_llm_config(llm_data: dict[str, Any] | None) -> LLMConfig:
    """Parse current multi-provider and legacy single-provider LLM config."""
    if not llm_data:
        return _default_llm_config()

    providers_data = llm_data.get("providers")
    providers: tuple[LLMProviderConfig, ...] = ()
    if providers_data:
        providers = _parse_llm_providers(providers_data)

    fallback_order = llm_data.get("fallback_order", ("kimi", "glm", "deepseek"))
    if isinstance(fallback_order, str):
        fallback_order = tuple(part.strip() for part in fallback_order.split(",") if part.strip())
    else:
        fallback_order = tuple(fallback_order)

    return LLMConfig(
        base_url=llm_data.get("base_url", ""),
        api_key_env=llm_data.get("api_key_env", ""),
        model=llm_data.get("model", ""),
        temperature=llm_data.get("temperature", 0.7),
        max_tokens=llm_data.get("max_tokens", 4096),
        request_timeout=llm_data.get("request_timeout", 120),
        max_retries=llm_data.get("max_retries", 3),
        default_provider=llm_data.get("default_provider", "kimi"),
        fallback_order=fallback_order,
        providers=providers,
    )


def _parse_llm_providers(providers_data: Any) -> tuple[LLMProviderConfig, ...]:
    if isinstance(providers_data, dict):
        items = providers_data.items()
    elif isinstance(providers_data, list):
        items = (
            (provider.get("name", f"provider_{idx}"), provider)
            for idx, provider in enumerate(providers_data)
        )
    else:
        raise ValueError("llm.providers must be an object or list")

    providers: list[LLMProviderConfig] = []
    for name, provider_data in items:
        if not isinstance(provider_data, dict):
            raise ValueError(f"llm.providers.{name} must be an object")
        providers.append(
            LLMProviderConfig(
                name=provider_data.get("name", name),
                base_url=provider_data["base_url"],
                api_key_env=provider_data["api_key_env"],
                model=provider_data["model"],
                enabled=provider_data.get("enabled", True),
                temperature=provider_data.get("temperature"),
                max_tokens=provider_data.get("max_tokens"),
                request_timeout=provider_data.get("request_timeout"),
                max_retries=provider_data.get("max_retries"),
                extra_body=provider_data.get("extra_body", {}),
                max_context_tokens=provider_data.get("max_context_tokens"),
            )
        )
    return tuple(providers)


def _default_llm_config() -> LLMConfig:
    return LLMConfig(
        base_url="https://api.kimi.com/coding/v1",
        api_key_env="KIMI_API_KEY",
        model="kimi-for-coding",
        temperature=0.7,
        max_tokens=32768,
        request_timeout=120,
        max_retries=3,
        default_provider="kimi",
        fallback_order=("kimi", "glm", "deepseek"),
        providers=(
            LLMProviderConfig(
                name="kimi",
                base_url="https://api.kimi.com/coding",
                api_key_env="KIMI_API_KEY",
                model="kimi-2.6",
            ),
            LLMProviderConfig(
                name="glm",
                base_url="https://open.bigmodel.cn/api/anthropic",
                api_key_env="GLM_API_KEY",
                model="glm-5.1",
            ),
            LLMProviderConfig(
                name="deepseek",
                base_url="https://api.deepseek.com/anthropic",
                api_key_env="DEEPSEEK_API_KEY",
                model="deepseek-v4-pro",
            ),
        ),
    )


def _default_config() -> EvolutionConfig:
    return EvolutionConfig(
        llm=_default_llm_config(),
    )
