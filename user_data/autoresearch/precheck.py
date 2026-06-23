"""
AutoResearch for Freqtrade - Pre-Evolution Check

Runs baseline health assessment and displays comprehensive pre-check report
before starting evolution. Waits for user confirmation.
"""

import json
import logging
import re
from pathlib import Path

from config import EvolutionConfig
from tracker import BacktestMetrics


logger = logging.getLogger(__name__)


def ensure_db_directory(config: EvolutionConfig) -> Path:
    """Ensure the database directory exists and return the db file path."""
    db_dir = config.db_path_resolved
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / f"{config.strategy_name}.sqlite"
    return db_file


def assess_health(
    is_metrics: BacktestMetrics,
    oos_metrics: BacktestMetrics,
) -> list[str]:
    """Assess baseline health and return a list of warning strings."""
    warnings: list[str] = []

    is_losing = is_metrics.profit_total_abs <= 0
    oos_losing = oos_metrics.profit_total_abs <= 0
    if is_losing and oos_losing:
        warnings.append(
            "Baseline strategy is losing on both IS and OOS periods"
        )

    if is_metrics.sharpe < -1.0:
        warnings.append(
            f"IS Sharpe is very negative ({is_metrics.sharpe:.2f}), "
            "suggest reviewing strategy logic"
        )
    if oos_metrics.sharpe < -1.0:
        warnings.append(
            f"OOS Sharpe is very negative ({oos_metrics.sharpe:.2f}), "
            "strategy may be fundamentally flawed"
        )

    if is_metrics.sharpe > 0 and oos_metrics.sharpe < is_metrics.sharpe * 0.3:
        warnings.append(
            f"Large IS/OOS gap: IS Sharpe={is_metrics.sharpe:.2f} vs "
            f"OOS Sharpe={oos_metrics.sharpe:.2f}"
        )

    if is_metrics.total_trades < 30:
        warnings.append(
            f"IS trade count too low ({is_metrics.total_trades}) for "
            "statistical significance (need >= 30)"
        )
    if oos_metrics.total_trades < 10:
        warnings.append(
            f"OOS trade count very low ({oos_metrics.total_trades}), "
            "results may not be reliable"
        )

    if is_metrics.max_drawdown_account > 0.30:
        warnings.append(
            f"IS max drawdown is high ({is_metrics.max_drawdown_account:.1%})"
        )
    if oos_metrics.max_drawdown_account > 0.25:
        warnings.append(
            f"OOS max drawdown is high ({oos_metrics.max_drawdown_account:.1%})"
        )

    if is_metrics.profit_factor < 1.0:
        warnings.append(
            f"IS profit factor below 1.0 ({is_metrics.profit_factor:.2f})"
        )
    if oos_metrics.profit_factor < 1.0:
        warnings.append(
            f"OOS profit factor below 1.0 ({oos_metrics.profit_factor:.2f})"
        )

    return warnings


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _format_timerange(tr: str) -> tuple[str, int]:
    """Parse timerange string and return (human-readable, days)."""
    parts = tr.split("-")
    if len(parts) != 2 or len(parts[0]) < 8 or len(parts[1]) < 8:
        return tr, 0

    start_str = parts[0][:8]
    end_str = parts[1][:8]
    try:
        from datetime import datetime
        start = datetime.strptime(start_str, "%Y%m%d")
        end = datetime.strptime(end_str, "%Y%m%d")
        days = (end - start).days
        human = (
            f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]} ~ "
            f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}"
        )
        return human, days
    except ValueError:
        return tr, 0


def _load_strategy_params(config: EvolutionConfig) -> dict:
    """Load strategy parameter file for display."""
    params_file = Path(config.strategy_path).with_suffix(".json")
    if not params_file.exists():
        return {}
    try:
        data = json.loads(params_file.read_text())
        return data.get("params", data)
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_param_ranges_from_source(source_code: str) -> dict[str, tuple]:
    """Extract parameter definitions with ranges from source code."""
    ranges: dict[str, tuple] = {}
    param_types = {
        "IntParameter", "DecimalParameter", "CategoricalParameter", "BooleanParameter",
    }
    pattern = re.compile(
        r"(\w+)\s*=\s*("
        + "|".join(param_types)
        + r")\s*\(([^)]+)\)",
        re.MULTILINE,
    )
    for match in pattern.finditer(source_code):
        name = match.group(1)
        args_str = match.group(3)
        parts = [p.strip() for p in args_str.split(",")]
        low = high = default = None
        for part in parts:
            if part.startswith("default="):
                try:
                    default = float(part.split("=", 1)[1])
                except ValueError:
                    pass
            elif part.startswith("space="):
                continue
            elif low is None:
                try:
                    low = float(part)
                except ValueError:
                    pass
            elif high is None:
                try:
                    high = float(part)
                except ValueError:
                    pass
        if low is not None and high is not None:
            ranges[name] = (low, high, default)
    return ranges


def format_backtest_section(
    metrics: BacktestMetrics,
    label: str,
) -> list[str]:
    """Format backtest metrics into display lines."""
    lines = [f"[{label}]"]

    pnl_sign = "+" if metrics.profit_total_abs > 0 else ""
    pnl_status = "  [!]" if metrics.profit_total_abs <= 0 else ""
    lines.append(
        f"  Total PnL:     {pnl_sign}{metrics.profit_total_abs:.2f} USDT "
        f"({pnl_sign}{metrics.profit_total_pct:.2f}%){pnl_status}"
    )

    sharpe_status = "  [!]" if metrics.sharpe < 0 else ""
    lines.append(f"  Sharpe:        {metrics.sharpe:.2f}{sharpe_status}")
    lines.append(f"  Sortino:       {metrics.sortino:.2f}")
    lines.append(f"  Calmar:        {metrics.calmar:.2f}")
    lines.append(f"  Profit Factor: {metrics.profit_factor:.2f}")
    lines.append(f"  Total Trades:  {metrics.total_trades}")

    win_count = int(metrics.total_trades * metrics.win_rate)
    lose_count = metrics.total_trades - win_count
    lines.append(
        f"  Win Rate:      {metrics.win_rate:.1%} ({win_count}W / {lose_count}L)"
    )
    lines.append(
        f"  Max Drawdown:  {metrics.max_drawdown_account:.2%} "
        f"({metrics.max_drawdown_abs:.2f} USDT)"
    )
    lines.append(
        f"  Avg Duration:  {_format_duration(metrics.avg_trade_duration_s)}"
    )

    return lines


def format_precheck_report(
    config: EvolutionConfig,
    is_metrics: BacktestMetrics,
    oos_metrics: BacktestMetrics,
    warnings: list[str],
    blacklist_rules: list,
    source_code: str,
) -> str:
    """Format the complete pre-evolution check report."""
    param_ranges = _extract_param_ranges_from_source(source_code)

    lines = [
        "=" * 60,
        "AutoResearch Pre-Evolution Check",
        "=" * 60,
        "",
        "[File Paths]",
        f"  Strategy:     {config.strategy_path}",
        f"  Params:       {Path(config.strategy_path).with_suffix('.json')}",
        f"  Freqtrade:    {config.base_config_path}",
        f"  Database:     {config.db_path_resolved / f'{config.strategy_name}.sqlite'}",
        f"  Results:      {config.results_file}",
        f"  Output:       {config.strategy_output_dir}",
        "",
        "[Strategy Info]",
        f"  Name:         {config.strategy_name}",
        f"  Timeframe:    {config.timeframe}",
        f"  Pairs:        {', '.join(config.pairs)}",
    ]

    if param_ranges:
        lines.append(f"  Hyperopt Params: {len(param_ranges)} total")
        for name, (low, high, default) in sorted(param_ranges.items()):
            default_str = f" = {default}" if default is not None else ""
            lines.append(f"    {name} [{low}, {high}]{default_str}")

    lines.append("")

    # IS backtest
    is_label = (
        f"Baseline IS - {config.in_sample_timerange}"
    )
    lines.extend(format_backtest_section(is_metrics, is_label))
    lines.append("")

    # OOS backtest
    oos_label = (
        f"Baseline OOS - {config.out_of_sample_timerange}"
    )
    lines.extend(format_backtest_section(oos_metrics, oos_label))
    lines.append("")

    # Health warnings
    if warnings:
        lines.append("[Health Assessment]")
        for w in warnings:
            lines.append(f"  [!] {w}")
        lines.append("")

    # Evolution config
    lines.extend(_format_evolution_config(config))

    # Blacklist rules
    if blacklist_rules:
        lines.append(f"[Blacklist Rules: {len(blacklist_rules)}]")
        for rule in blacklist_rules:
            lines.append(f"  {rule.id}: {rule.description}")
        lines.append("")

    lines.extend([
        "=" * 60,
        "Confirm start evolution? [y/N]",
        "=" * 60,
    ])

    return "\n".join(lines)


def _format_evolution_config(config: EvolutionConfig) -> list[str]:
    """Format evolution configuration section."""
    llm = config.llm
    hc = config.hyperopt
    sc = config.safety

    lines = [
        "[Evolution Config]",
        f"  Mode:             {config.evolution_mode}",
        f"  Max Iterations:   {config.max_iterations}",
        f"  Max Runtime:      {config.max_walltime_hours}h",
    ]

    if config.evolution_mode == "parameter_first":
        lines.extend([
            f"  Param Convergence:{config.param_convergence_rounds} rounds",
            f"  Improvement Thre: {config.param_improvement_threshold:.0%}",
            f"  Stagnation Limit: {config.stagnation_limit} rounds",
        ])

    lines.extend([
        f"  Max Restarts:     {config.max_restarts}",
        "",
        "  LLM Config:",
        f"    Default:        {llm.default_provider}",
    ])

    if llm.fallback_order:
        order_str = " -> ".join(llm.fallback_order)
        lines.append(f"    Fallback:       {order_str}")
    lines.append(f"    Temperature:    {llm.temperature}")
    lines.append(f"    Max Tokens:     {llm.max_tokens}")

    lines.extend([
        "",
        "  Hyperopt Config:",
        f"    Epochs:         {hc.epochs} (dynamic range: {config.epochs_floor}~{config.epochs_ceiling})",
        f"    Spaces:         {', '.join(hc.spaces)}",
        f"    Jobs:           {hc.jobs}",
        f"    Loss Function:  {hc.loss_function}",
    ])
    if hc.early_stop > 0:
        lines.append(f"    Early Stop:     {hc.early_stop} epochs")

    lines.extend([
        "",
        "  Safety Limits:",
        f"    Max Drawdown:   {sc.max_drawdown_pct:.0%}",
        f"    Min IS Trades:  {sc.min_trade_count}",
        f"    Min OOS Trades: {sc.min_oos_trade_count}",
        f"    Max OOS DD:     {sc.max_oos_drawdown_pct:.0%}",
        f"    Require OOS +:  {'Yes' if sc.require_oos_positive else 'No'}",
    ])

    return lines


def run_precheck(
    config: EvolutionConfig,
    is_metrics: BacktestMetrics,
    oos_metrics: BacktestMetrics,
    blacklist_rules: list,
    source_code: str,
    confirm: bool = True,
) -> bool:
    """Run pre-evolution check and optionally wait for user confirmation.

    Returns True if evolution should proceed.
    """
    ensure_db_directory(config)
    warnings = assess_health(is_metrics, oos_metrics)

    report = format_precheck_report(
        config, is_metrics, oos_metrics, warnings, blacklist_rules, source_code,
    )
    print(report)

    if not confirm:
        return True

    try:
        answer = input().strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
