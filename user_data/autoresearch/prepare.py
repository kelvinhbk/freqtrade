"""
AutoResearch for Freqtrade - Data Preparation & Baseline

Fixed foundation (never mutated by the agent).
Loads config, prepares Backtesting, establishes baseline metrics.
"""

import copy
import logging
from pathlib import Path

from config import EvolutionConfig
from tracker import BacktestMetrics

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.configuration import TimeRange
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.optimize_reports import generate_backtest_stats
from freqtrade.resolvers.strategy_resolver import StrategyResolver


logger = logging.getLogger(__name__)


def load_base_config(config: EvolutionConfig) -> dict:
    """Load freqtrade config for backtesting."""
    config_path = Path(config.base_config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

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
    if config.hyperopt.fee is not None:
        args["fee"] = config.hyperopt.fee

    # Inject db-url for trade persistence
    db_dir = config.db_path_resolved
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / f"{config.strategy_name}.sqlite"
    args["db_url"] = f"sqlite:///{db_file}"

    return setup_optimize_configuration(args, RunMode.BACKTEST)


def run_backtest(
    bt: Backtesting,
    base_config: dict,
    strategy_name: str,
    timerange_str: str,
    iteration: int = 0,
) -> tuple[BacktestMetrics, dict]:
    """Run a single backtest, return (metrics, raw_stats)."""
    cfg = copy.deepcopy(base_config)
    cfg["strategy"] = strategy_name
    cfg["timerange"] = timerange_str
    if "freqai" in cfg:
        cfg["freqai"]["identifier"] = f"autoresearch-{iteration:04d}"

    timerange = TimeRange.parse_timerange(timerange_str)
    bt.config = cfg
    bt.timerange = timerange
    data, _ = bt.load_bt_data()

    strategy = StrategyResolver.load_strategy(cfg)
    bt.strategylist = [strategy]
    bt.results = None
    bt.all_bt_content = {}

    min_date, max_date = bt.backtest_one_strategy(strategy, data, timerange)

    stats = generate_backtest_stats(
        data, bt.all_bt_content, min_date=min_date, max_date=max_date,
    )
    strat_stats = stats["strategy"][strategy_name]
    metrics = _extract_metrics(strat_stats, strategy_name)
    return metrics, strat_stats


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


def establish_baseline(
    config: EvolutionConfig,
    run_hyperopt: bool = True,
) -> tuple[BacktestMetrics, BacktestMetrics, dict, Backtesting]:
    """Run baseline on IS + OOS periods.

    If no existing params file and run_hyperopt=True, runs hyperopt first
    so the baseline uses optimized parameters for fair comparison.

    Returns (is_metrics, oos_metrics, base_config, bt_instance).
    """
    base_config = load_base_config(config)
    bt = Backtesting(base_config)

    strategy_name = config.strategy_name
    # Baseline params live next to the original strategy file, not in the evolution output dir
    params_file = Path(config.strategy_path).with_suffix(".json")

    if run_hyperopt and not params_file.exists():
        logger.info("No existing params found. Running baseline hyperopt...")
        from hyperopt_runner import run_hyperopt

        run_hyperopt(
            config,
            strategy_name,
            config.in_sample_timerange,
            list(config.hyperopt.spaces),
            0,
        )
    elif params_file.exists():
        logger.info(f"Using existing params: {params_file}")

    logger.info(f"Baseline: {strategy_name}")
    is_metrics, _ = run_backtest(
        bt, base_config, strategy_name, config.in_sample_timerange, 0
    )
    oos_metrics, _ = run_backtest(
        bt, base_config, strategy_name, config.out_of_sample_timerange, 0
    )

    logger.info(
        f"IS  Sharpe={is_metrics.sharpe:.3f}  PF={is_metrics.profit_factor:.3f}  "
        f"Trades={is_metrics.total_trades}  PnL={is_metrics.profit_total_abs:.2f}"
    )
    logger.info(
        f"OOS Sharpe={oos_metrics.sharpe:.3f}  PnL={oos_metrics.profit_total_abs:.2f}"
    )

    return is_metrics, oos_metrics, base_config, bt
