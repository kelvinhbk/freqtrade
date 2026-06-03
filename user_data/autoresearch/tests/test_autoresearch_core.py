# ruff: noqa: E402, S101

import sys
from pathlib import Path


AUTORESEARCH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTORESEARCH_DIR))

from config import (
    EvolutionConfig,
    HyperoptConfig,
    LLMConfig,
    LLMProviderConfig,
    SafetyConfig,
    ScoreWeights,
    load_evolution_config,
)
from evaluator import compute_composite_score
from hyperopt_runner import _build_hyperopt_args
from mutator import Mutator, _build_learning_notes, _build_prompt
from prepare import load_base_config
from safety import check_baseline_relative_validation
from tracker import BacktestMetrics, ExperimentRecord, ExperimentTracker, MutationRecord, StrategySnapshot


BASE_SOURCE = """
class Strategy:
    buy_rsi = IntParameter(10, 40, default=20, space="buy")
    sell_rsi = IntParameter(60, 90, default=70, space="sell")
    stoploss = -0.10

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi"] = 1
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return None

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit,
                        after_fill, **kwargs):
        return -0.10
"""


def metrics(
    *,
    sharpe: float,
    profit_factor: float,
    calmar: float,
    win_rate: float,
    trades: int,
    profit: float,
    drawdown: float = 0.05,
) -> BacktestMetrics:
    return BacktestMetrics(
        strategy_name="Strategy",
        total_trades=trades,
        profit_total_abs=profit,
        profit_total_pct=profit / 10,
        sharpe=sharpe,
        sortino=sharpe,
        calmar=calmar,
        max_drawdown_account=drawdown,
        max_drawdown_abs=drawdown * 1000,
        profit_factor=profit_factor,
        win_rate=win_rate,
        avg_trade_duration_s=1200,
        trades_per_day=1.0,
    )


def record(
    *,
    iteration: int,
    status: str,
    in_sample: BacktestMetrics | None = None,
    out_of_sample: BacktestMetrics | None = None,
    reason: str | None = None,
    analysis: str = "",
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=f"ar-{iteration:04d}",
        iteration=iteration,
        timestamp="2026-05-22T00:00:00+00:00",
        generation=0,
        mutation=MutationRecord(
            "llm_driven" if iteration else "baseline",
            "Add trend filter and volume confirmation" if iteration else "Initial baseline",
            llm_analysis=analysis,
        ),
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        composite_score=1.0 if status == "baseline" else 0.0,
        status=status,
        rejection_reason=reason,
    )


def test_detect_changed_spaces_includes_custom_exit() -> None:
    mutated = BASE_SOURCE.replace("return None", 'return "exit_now"', 1)

    assert Mutator.detect_changed_spaces(BASE_SOURCE, mutated) == ["sell"]


def test_learning_notes_use_recent_failures_without_boilerplate_confirmation() -> None:
    baseline_is = metrics(
        sharpe=5.0, profit_factor=1.3, calmar=18, win_rate=0.57, trades=1400, profit=920
    )
    baseline_oos = metrics(
        sharpe=3.1, profit_factor=1.35, calmar=45, win_rate=0.57, trades=135, profit=78
    )
    failed_oos = metrics(
        sharpe=-0.5, profit_factor=0.45, calmar=-10, win_rate=0.75, trades=9, profit=-17
    )
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
        record(
            iteration=1,
            status="regression",
            in_sample=baseline_is,
            out_of_sample=failed_oos,
            reason="OOS trades 9 < 10",
            analysis="Added EMA trend filter, ADX filter, and volume confirmation.",
        ),
    ]

    notes = _build_learning_notes(history)
    prompt = _build_prompt(
        EvolutionConfig(llm=LLMConfig(), mutation_focus="Exit-only run"),
        BASE_SOURCE,
        baseline_is,
        None,
        history,
    )

    assert "LESSONS FROM RECENT EXPERIMENTS" in notes
    assert "OOS trade opportunity collapsed" in notes
    assert "trend filters" in notes
    assert "volume gates" in notes
    assert "explicitly confirm" not in prompt
    assert "which failed pattern you are avoiding" in prompt


def test_detect_changed_spaces_includes_indicator_dependent_spaces() -> None:
    mutated = BASE_SOURCE.replace('dataframe["rsi"] = 1', 'dataframe["rsi"] = 2')

    assert Mutator.detect_changed_spaces(BASE_SOURCE, mutated) == ["buy", "sell"]


def test_detect_changed_spaces_includes_parameter_definition_changes() -> None:
    mutated = BASE_SOURCE.replace(
        'sell_rsi = IntParameter(60, 90, default=70, space="sell")',
        'sell_rsi = IntParameter(65, 95, default=75, space="sell")',
    )

    assert Mutator.detect_changed_spaces(BASE_SOURCE, mutated) == ["sell"]


def test_cleanup_strategy_removes_params_and_cached_bytecode(tmp_path: Path) -> None:
    strategy_path = tmp_path / "Candidate.py"
    params_path = tmp_path / "Candidate.json"
    cache_dir = tmp_path / "__pycache__"
    cache_path = cache_dir / "Candidate.cpython-313.pyc"

    strategy_path.write_text("class Candidate: pass")
    params_path.write_text("{}")
    cache_dir.mkdir()
    cache_path.write_text("cached")

    mutator = object.__new__(Mutator)
    mutator.cleanup_strategy(strategy_path)

    assert not strategy_path.exists()
    assert not params_path.exists()
    assert not cache_path.exists()


def test_composite_score_prefers_oos_robustness() -> None:
    baseline_is = metrics(
        sharpe=2.0, profit_factor=1.4, calmar=20, win_rate=0.65, trades=200, profit=300
    )
    baseline_oos = metrics(
        sharpe=1.0, profit_factor=1.2, calmar=10, win_rate=0.60, trades=80, profit=80
    )

    fragile_is = metrics(
        sharpe=8.0, profit_factor=2.5, calmar=80, win_rate=0.85, trades=400, profit=1000
    )
    fragile_oos = metrics(
        sharpe=-1.0,
        profit_factor=0.8,
        calmar=-5,
        win_rate=0.45,
        trades=80,
        profit=-50,
        drawdown=0.20,
    )
    robust_is = metrics(
        sharpe=4.0, profit_factor=1.8, calmar=40, win_rate=0.72, trades=220, profit=500
    )
    robust_oos = metrics(
        sharpe=3.0, profit_factor=1.6, calmar=35, win_rate=0.70, trades=90, profit=180
    )

    weights = ScoreWeights()
    fragile_score = compute_composite_score(
        fragile_is, fragile_oos, baseline_is, weights, baseline_oos=baseline_oos
    )
    robust_score = compute_composite_score(
        robust_is, robust_oos, baseline_is, weights, baseline_oos=baseline_oos
    )

    assert robust_score > fragile_score


def test_composite_score_is_baseline_relative_and_profit_aware() -> None:
    baseline_is = metrics(
        sharpe=5.02,
        profit_factor=1.33,
        calmar=18.22,
        win_rate=0.566,
        trades=1413,
        profit=920.98,
        drawdown=0.1665,
    )
    baseline_oos = metrics(
        sharpe=3.15,
        profit_factor=1.35,
        calmar=45.96,
        win_rate=0.570,
        trades=135,
        profit=77.72,
        drawdown=0.0363,
    )
    lower_profit_high_pf_oos = metrics(
        sharpe=2.26,
        profit_factor=1.74,
        calmar=51.94,
        win_rate=0.776,
        trades=49,
        profit=63.15,
        drawdown=0.0261,
    )
    lower_profit_high_pf_is = metrics(
        sharpe=2.41,
        profit_factor=1.54,
        calmar=28.19,
        win_rate=0.688,
        trades=471,
        profit=473.40,
        drawdown=0.0553,
    )
    better_live_oos = metrics(
        sharpe=2.99,
        profit_factor=1.58,
        calmar=67.31,
        win_rate=0.595,
        trades=84,
        profit=98.54,
        drawdown=0.0314,
    )
    better_live_is = metrics(
        sharpe=3.31,
        profit_factor=1.31,
        calmar=14.73,
        win_rate=0.591,
        trades=943,
        profit=819.18,
        drawdown=0.1831,
    )

    weights = ScoreWeights()
    baseline_score = compute_composite_score(
        baseline_is, baseline_oos, baseline_is, weights, baseline_oos=baseline_oos
    )
    lower_profit_score = compute_composite_score(
        lower_profit_high_pf_is,
        lower_profit_high_pf_oos,
        baseline_is,
        weights,
        baseline_oos=baseline_oos,
    )
    better_live_score = compute_composite_score(
        better_live_is, better_live_oos, baseline_is, weights, baseline_oos=baseline_oos
    )

    assert abs(baseline_score - 1.0) < 0.000001
    assert lower_profit_score < baseline_score
    assert better_live_score > baseline_score


def test_baseline_relative_validation_rejects_lower_oos_pnl() -> None:
    safety = SafetyConfig(
        min_oos_profit_vs_baseline=1.0,
        min_oos_sharpe_vs_baseline=0.85,
        min_oos_profit_factor_vs_baseline=1.0,
        max_oos_drawdown_vs_baseline=1.2,
        min_oos_trades_vs_baseline=0.4,
        min_is_profit_vs_baseline=0.6,
        min_is_sharpe_vs_baseline=0.6,
    )
    baseline_is = metrics(
        sharpe=5.0, profit_factor=1.3, calmar=18, win_rate=0.57, trades=1400, profit=920
    )
    baseline_oos = metrics(
        sharpe=3.1, profit_factor=1.35, calmar=45, win_rate=0.57, trades=135, profit=78
    )
    lower_oos_profit = metrics(
        sharpe=2.3, profit_factor=1.74, calmar=52, win_rate=0.78, trades=49, profit=63
    )

    ok, reason = check_baseline_relative_validation(
        baseline_is, lower_oos_profit, baseline_is, baseline_oos, safety
    )

    assert not ok
    assert "OOS PnL ratio" in reason


def test_baseline_relative_validation_allows_live_candidate_profile() -> None:
    safety = SafetyConfig(
        min_oos_profit_vs_baseline=1.0,
        min_oos_sharpe_vs_baseline=0.85,
        min_oos_profit_factor_vs_baseline=1.0,
        max_oos_drawdown_vs_baseline=1.2,
        min_oos_trades_vs_baseline=0.4,
        min_is_profit_vs_baseline=0.6,
        min_is_sharpe_vs_baseline=0.6,
    )
    baseline_is = metrics(
        sharpe=5.0, profit_factor=1.3, calmar=18, win_rate=0.57, trades=1400, profit=920
    )
    baseline_oos = metrics(
        sharpe=3.1, profit_factor=1.35, calmar=45, win_rate=0.57, trades=135, profit=78
    )
    candidate_is = metrics(
        sharpe=3.3, profit_factor=1.31, calmar=14, win_rate=0.59, trades=943, profit=819
    )
    candidate_oos = metrics(
        sharpe=3.0, profit_factor=1.58, calmar=67, win_rate=0.60, trades=84, profit=99
    )

    ok, _ = check_baseline_relative_validation(
        candidate_is, candidate_oos, baseline_is, baseline_oos, safety
    )

    assert ok


def test_llm_provider_order_prefers_default_and_fallbacks() -> None:
    config = LLMConfig(
        default_provider="kimi",
        fallback_order=("kimi", "glm", "deepseek"),
        providers=(
            LLMProviderConfig(
                name="deepseek",
                base_url="https://api.deepseek.com/anthropic",
                api_key_env="DEEPSEEK_API_KEY",
                model="deepseek-v4-pro",
            ),
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
        ),
    )

    assert [provider.name for provider in config.ordered_providers()] == [
        "kimi",
        "glm",
        "deepseek",
    ]


def test_load_evolution_config_parses_multi_provider_llm(tmp_path: Path) -> None:
    config_path = tmp_path / "evolution.json"
    config_path.write_text(
        """
{
  "llm": {
    "default_provider": "kimi",
    "fallback_order": ["kimi", "glm", "deepseek"],
    "temperature": 0.2,
    "providers": {
      "kimi": {
        "base_url": "https://api.kimi.com/coding",
        "api_key_env": "KIMI_API_KEY",
        "model": "kimi-2.6"
      },
      "glm": {
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "api_key_env": "GLM_API_KEY",
        "model": "glm-5.1"
      },
      "deepseek": {
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-pro"
      }
    }
  }
}
"""
    )

    config = load_evolution_config(config_path)

    assert config.llm.default_provider == "kimi"
    assert [provider.model for provider in config.llm.ordered_providers()] == [
        "kimi-2.6",
        "glm-5.1",
        "deepseek-v4-pro",
    ]
    assert [provider.base_url for provider in config.llm.ordered_providers()] == [
        "https://api.kimi.com/coding",
        "https://open.bigmodel.cn/api/anthropic",
        "https://api.deepseek.com/anthropic",
    ]


def test_load_evolution_config_parses_mutation_focus(tmp_path: Path) -> None:
    config_path = tmp_path / "evolution.json"
    config_path.write_text(
        """
{
  "llm": {
    "providers": {
      "kimi": {
        "base_url": "https://api.kimi.com/coding",
        "api_key_env": "KIMI_API_KEY",
        "model": "kimi-2.6"
      }
    }
  },
  "mutation_focus": "Exit-only run",
  "mutation_constraints": [
    "Do not change entry.",
    "Only sell-space changes."
  ]
}
"""
    )

    config = load_evolution_config(config_path)

    assert config.mutation_focus == "Exit-only run"
    assert config.mutation_constraints == (
        "Do not change entry.",
        "Only sell-space changes.",
    )


def test_hyperopt_args_include_candidate_strategy_path(tmp_path: Path) -> None:
    config_file = tmp_path / "freqtrade.json"
    config_file.write_text("{}")
    output_dir = tmp_path / "candidates"
    config = EvolutionConfig(
        llm=LLMConfig(),
        hyperopt=HyperoptConfig(epochs=10, jobs=8, spaces=("buy",)),
        strategy_path=str(tmp_path / "ENEW.py"),
        base_config_path=str(config_file),
        strategy_output_dir=str(output_dir),
        pairs=("BTC/USDT:USDT",),
        timeframe="5m",
    )

    args = _build_hyperopt_args(config, "ENEW_E0001", "20240101-20240201", ["buy"])

    assert args["strategy_path"] == config.strategy_output_dir


def test_backtest_config_includes_candidate_strategy_path(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "freqtrade.json"
    config_file.write_text("{}")
    output_dir = tmp_path / "candidates"
    config = EvolutionConfig(
        llm=LLMConfig(),
        strategy_path=str(tmp_path / "ENEW.py"),
        base_config_path=str(config_file),
        strategy_output_dir=str(output_dir),
        pairs=("BTC/USDT:USDT",),
        timeframe="5m",
    )
    captured = {}

    def fake_setup_optimize_configuration(args, runmode):
        captured.update(args)
        return {"ok": True}

    monkeypatch.setattr(
        "prepare.setup_optimize_configuration",
        fake_setup_optimize_configuration,
    )

    assert load_base_config(config) == {"ok": True}
    assert captured["strategy_path"] == config.strategy_output_dir


# --- Blacklist tests ---

def test_blacklist_add_and_load(tmp_path: Path) -> None:
    from blacklist import BlacklistRule, add_rule, load_blacklist, remove_rule

    config = EvolutionConfig(
        llm=LLMConfig(),
        strategy_path=str(tmp_path / "macd_v4.py"),
        blacklist_dir=str(tmp_path / "blacklists"),
    )
    rule = BlacklistRule(
        id="BL001",
        pattern="adding_trend_filter",
        description="No trend filter as entry gate",
        reason="15 failures",
        created_at="2026-05-31",
        failed_count=15,
    )
    add_rule(config, rule)

    rules = load_blacklist(config)
    assert len(rules) == 1
    assert rules[0].id == "BL001"
    assert rules[0].pattern == "adding_trend_filter"

    assert remove_rule(config, "BL001") is True
    assert remove_rule(config, "BL001") is False
    assert len(load_blacklist(config)) == 0


def test_blacklist_inject_into_prompt() -> None:
    from blacklist import BlacklistRule, inject_into_prompt

    rules = [
        BlacklistRule(id="BL001", pattern="test", description="No trend filters", failed_count=10),
    ]
    result = inject_into_prompt("Hello world", rules)
    assert "[BLACKLIST - MUST FOLLOW]" in result
    assert "BL001: No trend filters" in result
    assert "Hello world" in result

    assert inject_into_prompt("test", []) == "test"


def test_blacklist_detect_failure_pattern() -> None:
    from blacklist import detect_failure_pattern

    failures = [
        record(
            iteration=i,
            status="regression",
            in_sample=metrics(sharpe=2.0, profit_factor=1.3, calmar=10, win_rate=0.6, trades=100, profit=50),
            out_of_sample=metrics(sharpe=-0.5, profit_factor=0.8, calmar=-5, win_rate=0.4, trades=5, profit=-20),
            reason="OOS trades 5 < 10",
        )
        for i in range(1, 5)
    ]
    for f in failures:
        f.mutation.description = "Add EMA trend filter to entry"

    rule = detect_failure_pattern(failures)
    assert rule is not None
    assert rule.pattern == "adding_trend_filter"
    assert rule.failed_count >= 3


def test_blacklist_duplicate_id_skipped(tmp_path: Path) -> None:
    from blacklist import BlacklistRule, add_rule, load_blacklist

    config = EvolutionConfig(
        llm=LLMConfig(),
        strategy_path=str(tmp_path / "test.py"),
        blacklist_dir=str(tmp_path / "bl"),
    )
    rule = BlacklistRule(id="BL001", pattern="p", description="d")
    add_rule(config, rule)
    add_rule(config, rule)
    assert len(load_blacklist(config)) == 1


# --- Epochs Calculator tests ---

def test_calculate_epochs_dynamic() -> None:
    from epochs_calculator import calculate_epochs

    config = EvolutionConfig(llm=LLMConfig(), epochs_floor=200, epochs_ceiling=1000)

    assert calculate_epochs(2, 0.5, config) == 200
    assert calculate_epochs(50, 1.0, config) == 1000
    result = calculate_epochs(5, 1.0, config)
    assert 200 <= result <= 1000


def test_estimate_param_count() -> None:
    from epochs_calculator import estimate_param_count

    source = """
class Test:
    buy_rsi = IntParameter(10, 40, default=20, space="buy")
    sell_rsi = IntParameter(60, 90, default=70, space="sell")
    some_val = IntParameter(1, 10, default=5, space="roi")
"""
    assert estimate_param_count(source, ["buy"]) == 1
    assert estimate_param_count(source, ["buy", "sell"]) == 2
    assert estimate_param_count(source, ["buy", "sell", "roi"]) == 3


def test_calculate_dynamic_epochs_integration() -> None:
    from epochs_calculator import calculate_dynamic_epochs

    source = """
class Test:
    buy_rsi = IntParameter(10, 40, default=20, space="buy")
    sell_rsi = IntParameter(60, 90, default=70, space="sell")
"""
    config = EvolutionConfig(llm=LLMConfig(), epochs_floor=200, epochs_ceiling=1000)
    epochs = calculate_dynamic_epochs(source, ["buy", "sell"], config)
    assert 200 <= epochs <= 1000


# --- Precheck tests ---

def test_assess_health_warnings() -> None:
    from precheck import assess_health

    is_m = metrics(
        sharpe=-1.5, profit_factor=0.9, calmar=-10, win_rate=0.4,
        trades=50, profit=-100, drawdown=0.35,
    )
    oos_m = metrics(
        sharpe=-2.0, profit_factor=0.8, calmar=-15, win_rate=0.35,
        trades=5, profit=-50, drawdown=0.30,
    )

    warnings = assess_health(is_m, oos_m)
    assert any("losing on both" in w for w in warnings)
    assert any("IS Sharpe" in w for w in warnings)
    assert any("OOS Sharpe" in w for w in warnings)
    assert any("OOS trade count very low" in w for w in warnings)


def test_assess_health_no_warnings() -> None:
    from precheck import assess_health

    is_m = metrics(
        sharpe=2.0, profit_factor=1.5, calmar=10, win_rate=0.6,
        trades=200, profit=100, drawdown=0.10,
    )
    oos_m = metrics(
        sharpe=1.5, profit_factor=1.3, calmar=8, win_rate=0.58,
        trades=50, profit=30, drawdown=0.08,
    )

    warnings = assess_health(is_m, oos_m)
    assert len(warnings) == 0


def test_precheck_format_report() -> None:
    from precheck import format_precheck_report

    config = EvolutionConfig(
        llm=LLMConfig(),
        strategy_path="/path/to/macd_v4.py",
        base_config_path="/path/to/config.json",
        pairs=("BTC/USDT:USDT",),
        timeframe="15m",
        evolution_mode="parameter_first",
    )
    is_m = metrics(sharpe=2.0, profit_factor=1.5, calmar=10, win_rate=0.6, trades=200, profit=100)
    oos_m = metrics(sharpe=1.5, profit_factor=1.3, calmar=8, win_rate=0.58, trades=50, profit=30)

    report = format_precheck_report(config, is_m, oos_m, [], [], BASE_SOURCE)
    assert "Pre-Evolution Check" in report
    assert "parameter_first" in report
    assert "macd_v4.py" in report
    assert "BTC/USDT:USDT" in report


# --- Tracker stagnation/restart tests ---

def test_tracker_stagnation_and_restart(tmp_path: Path) -> None:
    results_file = tmp_path / "results.jsonl"
    config = EvolutionConfig(
        llm=LLMConfig(),
        strategy_path=str(tmp_path / "test.py"),
        results_file=str(results_file),
        strategy_output_dir=str(tmp_path / "output"),
    )
    tracker = ExperimentTracker(config)

    assert tracker.stagnation_count == 0
    assert tracker.restart_count == 0

    tracker.record_stagnation()
    tracker.record_stagnation()
    assert tracker.stagnation_count == 2

    tracker.record_restart("test reason", "expand_signals")
    assert tracker.restart_count == 1
    assert tracker.stagnation_count == 0
    assert len(tracker.restart_history) == 1
    assert tracker.restart_history[0]["direction"] == "expand_signals"


# --- Config new fields tests ---

def test_config_new_fields_defaults() -> None:
    config = EvolutionConfig(llm=LLMConfig())
    assert config.evolution_mode == "classic"
    assert config.param_convergence_rounds == 3
    assert config.param_improvement_threshold == 0.05
    assert config.stagnation_limit == 10
    assert config.max_restarts == 3
    assert config.epochs_floor == 200
    assert config.epochs_ceiling == 1000
    assert config.global_blacklist_threshold == 3


def test_config_loads_new_fields_from_json(tmp_path: Path) -> None:
    config_path = tmp_path / "evolution.json"
    config_path.write_text(
        """
{
  "llm": {"providers": {"k": {"base_url": "http://x", "api_key_env": "K", "model": "m"}}},
  "evolution_mode": "parameter_first",
  "param_convergence_rounds": 5,
  "stagnation_limit": 15,
  "epochs_floor": 300,
  "epochs_ceiling": 2000,
  "max_restarts": 5
}
"""
    )
    config = load_evolution_config(config_path)
    assert config.evolution_mode == "parameter_first"
    assert config.param_convergence_rounds == 5
    assert config.stagnation_limit == 15
    assert config.epochs_floor == 300
    assert config.epochs_ceiling == 2000
    assert config.max_restarts == 5


def test_hyperopt_args_with_epochs_override(tmp_path: Path) -> None:
    config_file = tmp_path / "freqtrade.json"
    config_file.write_text("{}")
    config = EvolutionConfig(
        llm=LLMConfig(),
        hyperopt=HyperoptConfig(epochs=500),
        strategy_path=str(tmp_path / "test.py"),
        base_config_path=str(config_file),
        pairs=("BTC/USDT:USDT",),
        timeframe="5m",
    )

    args_default = _build_hyperopt_args(config, "Test", "20240101-20240201", ["buy"])
    assert args_default["epochs"] == 500

    args_override = _build_hyperopt_args(config, "Test", "20240101-20240201", ["buy"], epochs=200)
    assert args_override["epochs"] == 200


def test_provider_config_max_context_tokens_default() -> None:
    provider = LLMProviderConfig(
        name="test",
        base_url="http://x",
        api_key_env="K",
        model="m",
    )
    assert provider.max_context_tokens is None


def test_provider_config_effective_max_context_tokens() -> None:
    provider = LLMProviderConfig(
        name="test",
        base_url="http://x",
        api_key_env="K",
        model="m",
        max_context_tokens=262144,
    )
    defaults = LLMConfig()
    assert provider.effective_max_context_tokens(defaults) == 262144

    no_override = LLMProviderConfig(
        name="test2",
        base_url="http://x",
        api_key_env="K",
        model="m2",
    )
    assert no_override.effective_max_context_tokens(defaults) == 200000


def test_load_config_parses_max_context_tokens(tmp_path: Path) -> None:
    config_path = tmp_path / "evolution.json"
    config_path.write_text(
        """
{
  "llm": {
    "providers": {
      "glm": {
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "api_key_env": "GLM_API_KEY",
        "model": "glm-5.1",
        "max_context_tokens": 262144
      }
    }
  }
}
"""
    )
    config = load_evolution_config(config_path)
    providers = config.llm.ordered_providers()
    assert providers[0].max_context_tokens == 262144


def test_estimate_tokens_empty_string() -> None:
    from mutator import estimate_tokens
    assert estimate_tokens("") == 0


def test_estimate_tokens_code() -> None:
    from mutator import estimate_tokens
    code = "def foo(x: int) -> int:\n    return x + 1\n"
    tokens = estimate_tokens(code)
    assert tokens > 0
    # Token estimate includes ~100 token buffer for overhead
    assert tokens > 100


def test_estimate_tokens_chinese() -> None:
    from mutator import estimate_tokens
    text = "这是一个测试文本，包含中文字符。"
    tokens = estimate_tokens(text)
    assert tokens > 0


def test_estimate_tokens_mixed() -> None:
    from mutator import estimate_tokens
    text = "def foo():\n    # 这是一个注释\n    return 42\n"
    tokens = estimate_tokens(text)
    assert tokens > 0


def test_token_limit_error_attributes() -> None:
    from mutator import TokenLimitError
    err = TokenLimitError("glm", "glm-5.1", 292352, 262144)
    assert err.provider == "glm"
    assert err.model == "glm-5.1"
    assert err.requested == 292352
    assert err.limit == 262144
    assert "glm" in str(err)


def test_build_history_table_max_rows_limits_output() -> None:
    from mutator import _build_history_table

    records_list = [
        record(
            iteration=i,
            status="regression",
            in_sample=metrics(sharpe=1.0, profit_factor=1.0, calmar=5, win_rate=0.5, trades=100, profit=10),
            out_of_sample=metrics(sharpe=0.5, profit_factor=0.9, calmar=2, win_rate=0.45, trades=20, profit=-5),
        )
        for i in range(20)
    ]
    full = _build_history_table(records_list)
    limited = _build_history_table(records_list, max_rows=5)
    assert full.count("|") > limited.count("|")
    assert len(limited.strip().split("\n")) == 7


def test_build_learning_notes_max_failures_limits_output() -> None:
    from mutator import _build_learning_notes

    baseline_is = metrics(sharpe=5.0, profit_factor=1.3, calmar=18, win_rate=0.57, trades=1400, profit=920)
    baseline_oos = metrics(sharpe=3.1, profit_factor=1.35, calmar=45, win_rate=0.57, trades=135, profit=78)
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
    ] + [
        record(
            iteration=i, status="regression", in_sample=baseline_is,
            out_of_sample=metrics(sharpe=-0.5, profit_factor=0.45, calmar=-10, win_rate=0.75, trades=9, profit=-17),
            reason="OOS trades too low", analysis="Added EMA trend filter",
        )
        for i in range(1, 9)
    ]
    full = _build_learning_notes(history)
    limited = _build_learning_notes(history, max_failures=3)
    assert "LESSONS FROM RECENT EXPERIMENTS" in limited
    assert limited.count("- Iter") <= 3


def test_extract_code_skeleton_preserves_imports_and_indicators() -> None:
    from mutator import _extract_code_skeleton

    source = """
from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

class TestStrategy(IStrategy):
    buy_rsi = IntParameter(10, 40, default=20, space="buy")
    stoploss = -0.10

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["rsi"] < self.buy_rsi.value),
            "enter_long",
        ] = 1
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        current_candle = dataframe.iloc[-1]
        if current_candle["rsi"] > 80:
            return "rsi_exit"
        return None
"""
    skeleton = _extract_code_skeleton(source)
    assert "from freqtrade.strategy import IStrategy" in skeleton
    assert "class TestStrategy(IStrategy):" in skeleton
    assert "buy_rsi = IntParameter" in skeleton
    assert "stoploss = -0.10" in skeleton
    assert "populate_indicators" in skeleton
    assert 'dataframe["rsi"]' in skeleton
    assert "[body preserved" in skeleton
    assert "enter_long" not in skeleton
    assert "rsi_exit" not in skeleton


def test_build_prompt_with_trim_params() -> None:
    baseline_is = metrics(sharpe=2.0, profit_factor=1.4, calmar=10, win_rate=0.6, trades=200, profit=100)
    baseline_oos = metrics(sharpe=1.0, profit_factor=1.2, calmar=5, win_rate=0.55, trades=50, profit=30)
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
    ] + [
        record(iteration=i, status="regression", in_sample=baseline_is,
               out_of_sample=metrics(sharpe=0.5, profit_factor=0.9, calmar=2, win_rate=0.45, trades=20, profit=-5))
        for i in range(1, 15)
    ]
    full = _build_prompt(EvolutionConfig(llm=LLMConfig()), BASE_SOURCE, baseline_is, None, history)
    trimmed = _build_prompt(EvolutionConfig(llm=LLMConfig()), BASE_SOURCE, baseline_is, None, history,
                            max_history=5, max_failures=3)
    assert len(full) > len(trimmed)


def test_propose_mutation_trims_prompt_when_over_budget(monkeypatch) -> None:
    """Verify propose_mutation rebuilds prompt with trimming when token budget is exceeded."""
    config = EvolutionConfig(
        llm=LLMConfig(
            max_tokens=32768,
            providers=(
                LLMProviderConfig(
                    name="test",
                    base_url="http://localhost",
                    api_key_env="TEST_KEY",
                    model="test-model",
                    max_context_tokens=1000,
                ),
            ),
        ),
    )
    mutator = Mutator(config)
    monkeypatch.setenv("TEST_KEY", "test-key")

    baseline_is = metrics(
        sharpe=2.0, profit_factor=1.4, calmar=10, win_rate=0.6, trades=200, profit=100
    )
    baseline_oos = metrics(
        sharpe=1.0, profit_factor=1.2, calmar=5, win_rate=0.55, trades=50, profit=30
    )
    snapshot = StrategySnapshot(
        strategy_name="Test",
        source_path=Path("/tmp/Test.py"),
        source_code=BASE_SOURCE,
        composite_score=0.5,
        in_sample=baseline_is,
        out_of_sample=baseline_oos,
        generation=1,
    )
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
    ]

    import httpx
    import mutator as mutator_mod

    calls: list[dict] = []
    original_build = mutator_mod._build_prompt

    def tracking_build(cfg, src, bl_is, snap, hist, **kwargs):
        calls.append(kwargs)
        return original_build(cfg, src, bl_is, snap, hist, **kwargs)

    monkeypatch.setattr(mutator_mod, "_build_prompt", tracking_build)

    def mock_post(self, url, **kwargs):
        request = httpx.Request("post", url)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "## ANALYSIS\ntest\n## CHANGES\n```python\nclass Test: pass\n```\n## EXPECTED_IMPACT\nnone"}],
                "stop_reason": "end_turn",
            },
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    mutator.propose_mutation(BASE_SOURCE, baseline_is, snapshot, history)

    # Should have been called with trimming (max_history < 20)
    assert any(c.get("max_history", 20) < 20 for c in calls)


def test_token_limit_error_detected_from_api_response(monkeypatch) -> None:
    """Verify that a 400 with token limit keywords triggers fallback to next provider."""
    config = EvolutionConfig(
        llm=LLMConfig(
            max_tokens=32768,
            providers=(
                LLMProviderConfig(
                    name="provider1",
                    base_url="http://localhost",
                    api_key_env="PROVIDER1_KEY",
                    model="model1",
                    max_context_tokens=200000,
                ),
                LLMProviderConfig(
                    name="provider2",
                    base_url="http://localhost",
                    api_key_env="PROVIDER2_KEY",
                    model="model2",
                    max_context_tokens=200000,
                ),
            ),
        ),
    )
    mutator = Mutator(config)
    monkeypatch.setenv("PROVIDER1_KEY", "key1")
    monkeypatch.setenv("PROVIDER2_KEY", "key2")

    baseline_is = metrics(
        sharpe=2.0, profit_factor=1.4, calmar=10, win_rate=0.6, trades=200, profit=100
    )
    baseline_oos = metrics(
        sharpe=1.0, profit_factor=1.2, calmar=5, win_rate=0.55, trades=50, profit=30
    )
    snapshot = StrategySnapshot(
        strategy_name="Test",
        source_path=Path("/tmp/Test.py"),
        source_code=BASE_SOURCE,
        composite_score=0.5,
        in_sample=baseline_is,
        out_of_sample=baseline_oos,
        generation=1,
    )
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
    ]

    import httpx

    call_count = 0

    def mock_post(self, url, **kwargs):
        nonlocal call_count
        call_count += 1
        request = httpx.Request("post", url)
        # First call (provider1) fails with token limit
        if call_count == 1:
            return httpx.Response(
                400,
                text='{"error": "Your request exceeded model token limit: 262144 (requested: 292352)"}',
                request=request,
            )
        # Second call (provider2) succeeds
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "## ANALYSIS\ntest\n## CHANGES\n```python\nclass Test: pass\n```\n## EXPECTED_IMPACT\nnone"}],
                "stop_reason": "end_turn",
            },
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    mutation, code = mutator.propose_mutation(BASE_SOURCE, baseline_is, snapshot, history)
    # Provider 1 should fail with token limit, provider 2 should succeed
    assert call_count == 2  # One call per provider
