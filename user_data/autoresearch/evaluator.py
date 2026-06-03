"""
AutoResearch for Freqtrade - Evaluator

Runs backtests and computes composite scores.
"""

import logging

from config import ScoreWeights
from prepare import run_backtest
from tracker import BacktestMetrics


logger = logging.getLogger(__name__)


def evaluate_strategy(
    bt,
    base_config: dict,
    strategy_name: str,
    timerange_str: str,
    iteration: int,
) -> tuple[BacktestMetrics, dict]:
    """Run backtest and return metrics + raw stats."""
    return run_backtest(bt, base_config, strategy_name, timerange_str, iteration)


def compute_composite_score(  # noqa: C901
    is_metrics: BacktestMetrics,
    oos_metrics: BacktestMetrics | None,
    baseline_is: BacktestMetrics,
    weights: ScoreWeights,
    baseline_oos: BacktestMetrics | None = None,
) -> float:
    """Baseline-relative live score.

    A baseline-equivalent profile scores 1.0. Candidates above 1.0 are better than
    baseline on the configured mix of profit, risk-adjusted return, drawdown, and
    sample size. Trade count is capped at baseline so the score does not reward
    over-trading by itself.
    """

    def clip(value: float, low: float = 0.0, high: float = 2.0) -> float:
        return max(low, min(high, value))

    def ratio_score(value: float, reference: float, high: float = 2.0) -> float:
        if reference > 0:
            return clip(value / reference, 0.0, high)
        if value > 0:
            return high
        if value == reference:
            return 1.0
        return 0.0

    def drawdown_score(metrics: BacktestMetrics, reference: BacktestMetrics) -> float:
        current_dd = metrics.max_drawdown_account
        reference_dd = reference.max_drawdown_account
        if current_dd <= 0:
            return 2.0 if reference_dd > 0 else 1.0
        if reference_dd <= 0:
            return 0.0
        return clip(reference_dd / current_dd)

    def normalized_metric_weights() -> tuple[tuple[str, float], ...]:
        raw_weights = (
            ("profit", weights.profit),
            ("sharpe", weights.sharpe),
            ("profit_factor", weights.profit_factor),
            ("drawdown", weights.drawdown),
            ("trade_count", weights.trade_count),
            ("win_rate", weights.win_rate),
            ("calmar", weights.calmar),
        )
        positive_weights = tuple((name, max(0.0, weight)) for name, weight in raw_weights)
        total = sum(weight for _, weight in positive_weights)
        if total <= 0:
            return (("profit", 1.0),)
        return tuple((name, weight / total) for name, weight in positive_weights if weight > 0)

    def component_score(metrics: BacktestMetrics, reference: BacktestMetrics) -> float:
        metric_scores = {
            "profit": ratio_score(metrics.profit_total_abs, reference.profit_total_abs),
            "sharpe": ratio_score(metrics.sharpe, reference.sharpe),
            "profit_factor": ratio_score(metrics.profit_factor, reference.profit_factor),
            "drawdown": drawdown_score(metrics, reference),
            "trade_count": ratio_score(metrics.total_trades, reference.total_trades, high=1.0),
            "win_rate": ratio_score(metrics.win_rate, reference.win_rate, high=1.5),
            "calmar": ratio_score(metrics.calmar, reference.calmar),
        }
        return sum(
            weight * metric_scores[name] for name, weight in normalized_metric_weights()
        )

    is_score = component_score(is_metrics, baseline_is)
    if not oos_metrics:
        return is_score

    oos_reference = baseline_oos or baseline_is
    oos_score = component_score(oos_metrics, oos_reference)
    is_weight = max(0.0, weights.in_sample)
    oos_weight = max(0.0, weights.out_of_sample)
    split_total = is_weight + oos_weight
    if split_total <= 0:
        is_weight, oos_weight, split_total = 0.25, 0.75, 1.0

    score = (is_weight * is_score + oos_weight * oos_score) / split_total

    if oos_metrics.profit_total_abs <= 0 or oos_metrics.profit_factor <= 1.0:
        score -= 0.50

    return score


def score_to_str(score: float, is_m: BacktestMetrics, oos_m: BacktestMetrics | None) -> str:
    parts = [
        f"Score={score:.4f}",
        f"Sharpe={is_m.sharpe:.3f}",
        f"PF={is_m.profit_factor:.3f}",
        f"DD={is_m.max_drawdown_account:.1%}",
        f"Trades={is_m.total_trades}",
        f"PnL={is_m.profit_total_abs:.2f}",
    ]
    if oos_m:
        parts.append(f"OOS_Sharpe={oos_m.sharpe:.3f}")
        parts.append(f"OOS_PnL={oos_m.profit_total_abs:.2f}")
    return "  ".join(parts)
