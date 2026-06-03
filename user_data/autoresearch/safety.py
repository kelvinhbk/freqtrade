"""
AutoResearch for Freqtrade - Safety Gates

L1: AST static analysis
L2: In-sample hard limits
L3: Out-of-sample validation
L4: Walk-forward validation
"""

import ast
import logging
import re
from datetime import datetime, timedelta

from config import SafetyConfig
from tracker import BacktestMetrics


logger = logging.getLogger(__name__)


def check_static(source_code: str) -> tuple[bool, str]:  # noqa: C901
    """L1: AST-based static analysis before any backtest."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    has_class = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            has_class = True
            required = {"populate_indicators", "populate_entry_trend", "populate_exit_trend"}
            found = {
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            missing = required - found
            if missing:
                return False, f"Missing methods: {missing}"

        if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "call", "run"}:
            return False, f"Dangerous attribute: {node.attr}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"eval", "exec", "compile"}:
                return False, f"Dangerous call: {func.id}"

    # Check for common LLM mistakes in custom_exit/custom_stoploss
    # where .shift()/.rolling() is called on scalar values from iloc[-1]
    # Only check inside these two methods; populate_indicators() may legitimately
    # use Series methods like dataframe['close'].pct_change().
    def _find_method_body(class_node: ast.ClassDef, method_name: str) -> list[ast.stmt] | None:
        for item in class_node.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == method_name
            ):
                return item.body
        return None

    banned = {"shift", "rolling", "diff", "pct_change"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for method_name in ("custom_exit", "custom_stoploss"):
                body = _find_method_body(node, method_name)
                if body:
                    for stmt in body:
                        for child in ast.walk(stmt):
                            if isinstance(child, ast.Call):
                                func = child.func
                                if isinstance(func, ast.Attribute) and func.attr in banned:
                                    if isinstance(func.value, ast.Subscript):
                                        return False, (
                                            f"Invalid call: scalar value cannot use .{func.attr}() "
                                            f"in {method_name}(). Use pre-computed indicators in "
                                            "populate_indicators() instead."
                                        )

    # Check for dataframe['xxx'] used directly in if/elif/while conditions inside
    # custom_exit/custom_stoploss. This causes "The truth value of a Series is ambiguous"
    # because dataframe['col'] is a Series, not a scalar.
    def _find_dataframe_in_conditions(method_node: ast.FunctionDef) -> list[str]:
        errors: list[str] = []
        # Collect all Subscript nodes that are part of a Call chain
        # (e.g., dataframe['close'].iloc[-1] is OK)
        subscripts_in_calls: set[int] = set()
        for node in ast.walk(method_node):
            if isinstance(node, ast.Call):
                func = node.func
                while isinstance(func, ast.Attribute):
                    func = func.value
                if isinstance(func, ast.Subscript):
                    if isinstance(func.value, ast.Name) and func.value.id == "dataframe":
                        subscripts_in_calls.add(id(func))

        # Check If/While conditions
        for node in ast.walk(method_node):
            if isinstance(node, (ast.If, ast.While)):
                for sub in ast.walk(node.test):
                    if (
                        isinstance(sub, ast.Subscript)
                        and id(sub) not in subscripts_in_calls
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "dataframe"
                    ):
                        # Try to extract column name for a better error message
                        col_name = ""
                        if isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str):
                            col_name = f"['{sub.slice.value}']"
                        errors.append(
                            f"dataframe{col_name} used directly in a boolean condition. "
                            "In custom_exit/custom_stoploss, use current_candle[...] "
                            "(scalar) instead of dataframe[...] (Series)."
                        )
        return errors

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for method_name in ("custom_exit", "custom_stoploss"):
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == method_name
                    ):
                        cond_errors = _find_dataframe_in_conditions(item)
                        if cond_errors:
                            return False, cond_errors[0]

    # Check for trade.is_long which does not exist on freqtrade's LocalTrade
    if re.search(r"\btrade\.is_long\b", source_code):
        return (
            False,
            "Invalid attribute: trade.is_long does not exist. Use not trade.is_short instead.",
        )

    if not has_class:
        return False, "No class definition found"
    return True, "OK"


def check_hard_limits(
    metrics: BacktestMetrics, config: SafetyConfig
) -> tuple[bool, str]:
    """L2: In-sample hard limits."""
    checks = [
        (
            metrics.total_trades >= config.min_trade_count,
            f"Trades {metrics.total_trades} < {config.min_trade_count}",
        ),
        (
            metrics.max_drawdown_account <= config.max_drawdown_pct,
            f"DD {metrics.max_drawdown_account:.1%} > {config.max_drawdown_pct:.1%}",
        ),
        (
            metrics.profit_total_abs > 0,
            f"Unprofitable: {metrics.profit_total_abs:.2f}",
        ),
        (
            metrics.profit_factor > 1.0,
            f"PF {metrics.profit_factor:.2f} <= 1.0",
        ),
    ]
    for passed, reason in checks:
        if not passed:
            return False, reason
    return True, "OK"


def check_oos_validation(
    oos: BacktestMetrics, is_m: BacktestMetrics, config: SafetyConfig
) -> tuple[bool, str]:
    """L3: Out-of-sample validation."""
    checks = [
        (
            oos.total_trades >= config.min_oos_trade_count,
            f"OOS trades {oos.total_trades} < {config.min_oos_trade_count}",
        ),
        (
            oos.max_drawdown_account <= config.max_oos_drawdown_pct,
            f"OOS DD {oos.max_drawdown_account:.1%} > {config.max_oos_drawdown_pct:.1%}",
        ),
    ]
    if config.require_oos_positive:
        checks.append((
            oos.profit_total_abs > 0,
            f"OOS unprofitable: {oos.profit_total_abs:.2f}",
        ))
    checks.extend([
        (
            oos.profit_factor > 1.0,
            f"OOS PF {oos.profit_factor:.2f} <= 1.0",
        ),
        (
            oos.sharpe > 0,
            f"OOS Sharpe {oos.sharpe:.2f} <= 0",
        ),
    ])

    if is_m.sharpe > 0:
        degradation = is_m.sharpe / max(oos.sharpe, 0.01)
        checks.append((
            degradation < config.max_sharpe_degradation,
            f"Overfitting: IS/OOS sharpe = {degradation:.1f}x",
        ))

    for passed, reason in checks:
        if not passed:
            return False, reason
    return True, "OK"


def check_baseline_relative_validation(
    is_m: BacktestMetrics,
    oos: BacktestMetrics,
    baseline_is: BacktestMetrics,
    baseline_oos: BacktestMetrics,
    config: SafetyConfig,
) -> tuple[bool, str]:
    """Require candidates to beat or stay close to baseline on live-relevant metrics."""

    def _ratio(value: float, reference: float) -> float:
        if reference == 0:
            return float("inf") if value > 0 else 0.0
        return value / reference

    checks: list[tuple[bool, str]] = []
    if config.min_oos_profit_vs_baseline > 0:
        ratio = _ratio(oos.profit_total_abs, baseline_oos.profit_total_abs)
        checks.append((
            ratio >= config.min_oos_profit_vs_baseline,
            (
                f"OOS PnL ratio {ratio:.2f}x < "
                f"{config.min_oos_profit_vs_baseline:.2f}x baseline"
            ),
        ))
    if config.min_oos_sharpe_vs_baseline > 0 and baseline_oos.sharpe > 0:
        ratio = _ratio(oos.sharpe, baseline_oos.sharpe)
        checks.append((
            ratio >= config.min_oos_sharpe_vs_baseline,
            (
                f"OOS Sharpe ratio {ratio:.2f}x < "
                f"{config.min_oos_sharpe_vs_baseline:.2f}x baseline"
            ),
        ))
    if config.min_oos_profit_factor_vs_baseline > 0 and baseline_oos.profit_factor > 0:
        ratio = _ratio(oos.profit_factor, baseline_oos.profit_factor)
        checks.append((
            ratio >= config.min_oos_profit_factor_vs_baseline,
            (
                f"OOS PF ratio {ratio:.2f}x < "
                f"{config.min_oos_profit_factor_vs_baseline:.2f}x baseline"
            ),
        ))
    if config.max_oos_drawdown_vs_baseline > 0 and baseline_oos.max_drawdown_account > 0:
        ratio = _ratio(oos.max_drawdown_account, baseline_oos.max_drawdown_account)
        checks.append((
            ratio <= config.max_oos_drawdown_vs_baseline,
            (
                f"OOS DD ratio {ratio:.2f}x > "
                f"{config.max_oos_drawdown_vs_baseline:.2f}x baseline"
            ),
        ))
    if config.min_oos_trades_vs_baseline > 0 and baseline_oos.total_trades > 0:
        ratio = _ratio(oos.total_trades, baseline_oos.total_trades)
        checks.append((
            ratio >= config.min_oos_trades_vs_baseline,
            (
                f"OOS trades ratio {ratio:.2f}x < "
                f"{config.min_oos_trades_vs_baseline:.2f}x baseline"
            ),
        ))
    if config.min_is_profit_vs_baseline > 0:
        ratio = _ratio(is_m.profit_total_abs, baseline_is.profit_total_abs)
        checks.append((
            ratio >= config.min_is_profit_vs_baseline,
            (
                f"IS PnL ratio {ratio:.2f}x < "
                f"{config.min_is_profit_vs_baseline:.2f}x baseline"
            ),
        ))
    if config.min_is_sharpe_vs_baseline > 0 and baseline_is.sharpe > 0:
        ratio = _ratio(is_m.sharpe, baseline_is.sharpe)
        checks.append((
            ratio >= config.min_is_sharpe_vs_baseline,
            (
                f"IS Sharpe ratio {ratio:.2f}x < "
                f"{config.min_is_sharpe_vs_baseline:.2f}x baseline"
            ),
        ))

    for passed, reason in checks:
        if not passed:
            return False, reason
    return True, "OK"


def check_walk_forward(
    bt,
    base_config: dict,
    strategy_name: str,
    timerange_str: str,
    config: SafetyConfig,
    iteration: int,
) -> tuple[bool, str]:
    """L4: Walk-forward validation (expensive, final candidates only)."""
    from prepare import run_backtest

    parts = timerange_str.split("-")
    if len(parts) != 2:
        return True, "Skipped"

    try:
        start = datetime.strptime(parts[0][:8], "%Y%m%d")
        end = datetime.strptime(parts[1][:8], "%Y%m%d")
    except ValueError:
        return True, "Skipped"
    if end <= start:
        return True, "Skipped"

    splits = max(config.walk_forward_splits, 1)
    fold_len = (end - start) / splits
    profitable = 0
    evaluated = 0
    details: list[str] = []
    for i in range(splits):
        fold_start = start + i * fold_len
        fold_end = end if i == splits - 1 else start + (i + 1) * fold_len
        # Avoid zero-length folds after date truncation.
        if fold_end.date() <= fold_start.date():
            fold_end = fold_start + timedelta(days=1)
        fold_tr = f"{fold_start:%Y%m%d}-{fold_end:%Y%m%d}"
        try:
            metrics, _ = run_backtest(bt, base_config, strategy_name, fold_tr, iteration)
            evaluated += 1
            passed = (
                metrics.total_trades >= config.min_walk_forward_trades
                and metrics.profit_total_abs > 0
                and metrics.profit_factor > 1.0
                and metrics.max_drawdown_account <= config.max_oos_drawdown_pct
            )
            details.append(
                f"{i + 1}:{metrics.total_trades}t/{metrics.profit_total_abs:.1f}p/"
                f"{metrics.max_drawdown_account:.1%}dd"
            )
            if passed:
                profitable += 1
        except Exception as exc:
            details.append(f"{i + 1}:error={type(exc).__name__}")

    if evaluated == 0:
        return False, "Walk-forward failed: no folds evaluated"

    required = min(max(config.min_walk_forward_profitable, 1), splits)
    if profitable >= required:
        return True, f"Passed {profitable}/{splits} folds ({'; '.join(details)})"
    return False, f"Only {profitable}/{splits} folds passed; need {required} ({'; '.join(details)})"
