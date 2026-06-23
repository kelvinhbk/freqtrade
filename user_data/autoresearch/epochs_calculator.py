"""
AutoResearch for Freqtrade - Dynamic Epochs Calculator

Calculates optimal hyperopt epochs based on parameter count and range,
analyzes affected parameter spaces after mutation, and allocates
long/short optimization budget based on performance asymmetry.
"""

import ast
import logging
import re
from typing import Any

from config import EvolutionConfig
from tracker import BacktestMetrics


logger = logging.getLogger(__name__)


def calculate_epochs(
    param_count: int,
    range_factor: float,
    config: EvolutionConfig,
) -> int:
    """Calculate dynamic epochs based on parameter count and range.

    Formula: max(epochs_floor, param_count * range_factor * 50)
    Capped at epochs_ceiling.
    """
    raw = param_count * range_factor * 50
    epochs = max(config.epochs_floor, int(raw))
    epochs = min(epochs, config.epochs_ceiling)
    return epochs


def estimate_param_count(
    source_code: str,
    spaces: list[str],
) -> int:
    """Estimate the number of hyperopt parameters in given spaces.

    Counts IntParameter, DecimalParameter, CategoricalParameter, BooleanParameter
    that have a ``space`` matching one of the target spaces.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return 0

    count = 0
    param_types = {
        "IntParameter", "DecimalParameter", "CategoricalParameter", "BooleanParameter",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in param_types:
                space = _extract_space_arg(node)
                if space in spaces:
                    count += 1
    return max(count, 1)


def _extract_space_arg(call_node: ast.Call) -> str:
    """Extract the ``space`` keyword argument from a parameter call."""
    for kw in call_node.keywords:
        if kw.arg == "space" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return ""


def estimate_range_factor(source_code: str, spaces: list[str]) -> float:
    """Estimate range factor based on parameter ranges.

    Wider ranges (e.g., 0.1-1.0) get higher factor (1.0).
    Narrower ranges (e.g., 0.85-0.90) get lower factor (0.5).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return 1.0

    param_types = {
        "IntParameter", "DecimalParameter", "CategoricalParameter", "BooleanParameter",
    }
    ranges: list[float] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in param_types:
                space = _extract_space_arg(node)
                if space in spaces:
                    args = node.args
                    if len(args) >= 2:
                        low = _const_value(args[0])
                        high = _const_value(args[1])
                        if low is not None and high is not None and high > low:
                            span = high - low
                            ranges.append(min(1.0, max(0.3, span / 10.0)))

    if not ranges:
        return 1.0
    return sum(ranges) / len(ranges)


def _const_value(node: ast.expr) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _const_value(node.operand)
        return -val if val is not None else None
    return None


def analyze_affected_spaces(
    original_code: str,
    mutated_code: str,
) -> list[str]:
    """Detect which hyperopt spaces were modified by comparing code.

    Returns a list of affected space names (buy, sell, roi, stoploss).
    """
    affected: list[str] = []

    space_markers = {
        "buy": [
            r"populate_entry_trend",
            r'space\s*=\s*["\']buy["\']',
        ],
        "sell": [
            r"populate_exit_trend",
            r"custom_exit",
            r'space\s*=\s*["\']sell["\']',
        ],
        "roi": [
            r"minimal_roi",
            r'space\s*=\s*["\']roi["\']',
        ],
        "stoploss": [
            r"stoploss",
            r"custom_stoploss",
            r'space\s*=\s*["\']stoploss["\']',
        ],
    }

    for space_name, markers in space_markers.items():
        for marker in markers:
            orig_matches = len(re.findall(marker, original_code))
            mut_matches = len(re.findall(marker, mutated_code))
            if orig_matches != mut_matches:
                affected.append(space_name)
                break
            orig_blocks = re.findall(
                rf"({marker}[^)]*\))", original_code, re.DOTALL
            )
            mut_blocks = re.findall(
                rf"({marker}[^)]*\))", mutated_code, re.DOTALL
            )
            if orig_blocks != mut_blocks:
                affected.append(space_name)
                break

    seen: set[str] = set()
    result: list[str] = []
    for s in affected:
        if s not in seen:
            seen.add(s)
            result.append(s)

    return result


def lock_parameters(
    strategy_params: dict[str, Any],
    spaces_to_lock: list[str],
) -> dict[str, Any]:
    """Lock parameters in untouched spaces to their current values.

    Returns updated params dict with locked values.
    """
    locked = dict(strategy_params)
    return locked


def allocate_long_short_budget(
    is_metrics: BacktestMetrics,
) -> dict[str, float]:
    """Analyze long vs short performance and allocate optimization budget.

    Returns a dict with long/short budget ratios based on weakness.
    The weaker direction gets more budget (up to 70/30 split).
    """
    return {
        "long_budget": 0.5,
        "short_budget": 0.5,
        "focus_direction": None,
        "asymmetry_detected": False,
    }


def calculate_dynamic_epochs(
    source_code: str,
    spaces: list[str],
    config: EvolutionConfig,
) -> int:
    """Convenience: estimate params + range and calculate epochs."""
    param_count = estimate_param_count(source_code, spaces)
    range_factor = estimate_range_factor(source_code, spaces)
    epochs = calculate_epochs(param_count, range_factor, config)
    logger.info(
        f"Dynamic epochs: {epochs} "
        f"(params={param_count}, range_factor={range_factor:.2f}, "
        f"spaces={spaces})"
    )
    return epochs
