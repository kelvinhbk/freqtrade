"""
Custom hyperopt loss function: Composite score with Sortino, Calmar, PF, and drawdown penalties.
Optimized for crypto futures strategies.
Place in: user_data/hyperopts/composite_crypto_loss.py
"""
from datetime import datetime
from pandas import DataFrame
from freqtrade.optimize.hyperopt import IHyperOptLoss
import numpy as np


class CompositeCryptoLoss(IHyperOptLoss):
    """
    Multi-objective loss function for crypto markets:
    - Maximize Sortino ratio (downside risk only)
    - Maximize Calmar ratio (return / max drawdown)
    - Penalize excessive drawdown (>15%)
    - Penalize low profit factor (<1.2)
    - Penalize insufficient trade count
    """

    @staticmethod
    def hyperopt_loss_function(
        *,
        results: DataFrame,
        trade_count: int,
        min_date: datetime,
        max_date: datetime,
        config: dict,
        processed: dict[str, DataFrame],
        backtest_stats: dict,
        starting_balance: float,
        **kwargs,
    ) -> float:
        total_profit = results["profit_abs"].sum()
        days = max((max_date - min_date).days, 1)

        daily_profit = results.groupby(results["open_date"].dt.date)["profit_abs"].sum()

        if len(daily_profit) < 10 or daily_profit.std() == 0 or trade_count < 30:
            return 100.0

        # Sortino ratio (downside deviation only)
        downside_returns = daily_profit[daily_profit < 0]
        downside_std = downside_returns.std() if len(downside_returns) > 0 else 1e-10
        sortino = (daily_profit.mean() / downside_std) * (365 ** 0.5) if downside_std > 0 else 0

        # Calmar ratio
        max_drawdown = backtest_stats.get("max_drawdown_abs", 0)
        drawdown_ratio = max_drawdown / starting_balance if starting_balance > 0 else 1
        total_return = total_profit / starting_balance if starting_balance > 0 else 0
        calmar = total_return / drawdown_ratio if drawdown_ratio > 0 else 0

        # Profit Factor
        gross_profit = results[results["profit_abs"] > 0]["profit_abs"].sum()
        gross_loss = abs(results[results["profit_abs"] < 0]["profit_abs"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 1e10

        # Win Rate
        wins = len(results[results["profit_abs"] > 0])
        win_rate = wins / trade_count if trade_count > 0 else 0

        # Max Consecutive Losses
        results_sorted = results.sort_values("open_date")
        loss_streaks = []
        current = 0
        for p in results_sorted["profit_abs"]:
            if p < 0:
                current += 1
            else:
                loss_streaks.append(current)
                current = 0
        max_consec = max(loss_streaks) if loss_streaks else 0

        # Normalize scores (0-1)
        sortino_score = min(sortino / 2.0, 1.0)
        calmar_score = min(calmar / 3.0, 1.0)
        pf_score = min(profit_factor / 2.0, 1.0)
        wr_score = min(win_rate / 0.6, 1.0)
        consec_score = max(0, 1.0 - max_consec / 10.0)

        composite = (
            0.30 * sortino_score
            + 0.25 * calmar_score
            + 0.20 * pf_score
            + 0.15 * wr_score
            + 0.10 * consec_score
        )

        # Penalties
        drawdown_penalty = max(0, drawdown_ratio - 0.15) * 20
        pf_penalty = max(0, 1.2 - profit_factor) * 10

        expected_trades_per_day = 1
        min_expected = expected_trades_per_day * days
        trade_penalty = max(0, (min_expected - trade_count) / min_expected) * 5

        if "stoploss" in config:
            stoploss_val = abs(config["stoploss"])
            if stoploss_val > 0.10:
                return 100.0

        loss = -composite * 10 + drawdown_penalty + pf_penalty + trade_penalty
        return loss
