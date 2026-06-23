"""
AutoResearch for Freqtrade - Experiment Tracker

Append-only JSONL experiment log with best-strategy tracking.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import EvolutionConfig


@dataclass
class BacktestMetrics:
    strategy_name: str
    total_trades: int
    profit_total_abs: float
    profit_total_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_account: float
    max_drawdown_abs: float
    profit_factor: float
    win_rate: float
    avg_trade_duration_s: float
    trades_per_day: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MutationRecord:
    mutation_type: str
    description: str
    llm_analysis: str = ""
    llm_expected_impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentRecord:
    experiment_id: str
    iteration: int
    timestamp: str
    generation: int
    mutation: MutationRecord
    in_sample: BacktestMetrics | None = None
    out_of_sample: BacktestMetrics | None = None
    composite_score: float = 0.0
    status: str = "pending"
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "experiment_id": self.experiment_id,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "generation": self.generation,
            "mutation": self.mutation.to_dict(),
            "composite_score": self.composite_score,
            "status": self.status,
            "rejection_reason": self.rejection_reason,
        }
        d["in_sample"] = self.in_sample.to_dict() if self.in_sample else None
        d["out_of_sample"] = (
            self.out_of_sample.to_dict() if self.out_of_sample else None
        )
        return d


@dataclass
class StrategySnapshot:
    strategy_name: str
    source_path: Path
    source_code: str
    composite_score: float
    in_sample: BacktestMetrics
    out_of_sample: BacktestMetrics
    generation: int


class ExperimentTracker:
    def __init__(self, config: EvolutionConfig):
        self.config = config
        self.results_path = config.results_path
        self._records: list[ExperimentRecord] = []
        self._best_snapshot: StrategySnapshot | None = None
        self._stagnation_count: int = 0
        self._restart_count: int = 0
        self._restart_history: list[dict[str, Any]] = []
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.results_path.exists():
            return
        with self.results_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = self._parse_record(data)
                    self._records.append(record)
                    if record.status == "improvement":
                        self._update_best_from_record(record)
                except (json.JSONDecodeError, KeyError):
                    continue

    def _parse_record(self, data: dict[str, Any]) -> ExperimentRecord:
        mut = MutationRecord(**data["mutation"])
        is_metrics = BacktestMetrics(**data["in_sample"]) if data.get("in_sample") else None
        oos_metrics = (
            BacktestMetrics(**data["out_of_sample"]) if data.get("out_of_sample") else None
        )
        return ExperimentRecord(
            experiment_id=data["experiment_id"],
            iteration=data["iteration"],
            timestamp=data["timestamp"],
            generation=data["generation"],
            mutation=mut,
            in_sample=is_metrics,
            out_of_sample=oos_metrics,
            composite_score=data.get("composite_score", 0.0),
            status=data["status"],
            rejection_reason=data.get("rejection_reason"),
        )

    def _update_best_from_record(self, record: ExperimentRecord) -> None:
        if record.in_sample is None or record.out_of_sample is None:
            return
        name = record.in_sample.strategy_name
        path = Path(self.config.strategy_output_dir) / f"{name}.py"
        code = path.read_text() if path.exists() else ""
        self._best_snapshot = StrategySnapshot(
            strategy_name=name,
            source_path=path,
            source_code=code,
            composite_score=record.composite_score,
            in_sample=record.in_sample,
            out_of_sample=record.out_of_sample,
            generation=record.generation,
        )

    def _append(self, record: ExperimentRecord) -> None:
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        self._records.append(record)

    def _make_id(self, iteration: int) -> str:
        return f"ar-{iteration:04d}-{uuid.uuid4().hex[:4]}"

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    @property
    def _generation(self) -> int:
        return self._best_snapshot.generation if self._best_snapshot else 0

    # -- Public API --

    def set_baseline(
        self,
        strategy_name: str,
        source_code: str,
        is_metrics: BacktestMetrics,
        oos_metrics: BacktestMetrics,
        score: float,
    ) -> None:
        path = Path(self.config.strategy_output_dir) / f"{strategy_name}.py"
        self._best_snapshot = StrategySnapshot(
            strategy_name=strategy_name,
            source_path=path,
            source_code=source_code,
            composite_score=score,
            in_sample=is_metrics,
            out_of_sample=oos_metrics,
            generation=0,
        )
        self._append(ExperimentRecord(
            experiment_id=self._make_id(0),
            iteration=0,
            timestamp=self._now_iso(),
            generation=0,
            mutation=MutationRecord("baseline", "Initial baseline"),
            in_sample=is_metrics,
            out_of_sample=oos_metrics,
            composite_score=score,
            status="baseline",
        ))

    def record_improvement(
        self,
        mutation: MutationRecord,
        is_metrics: BacktestMetrics,
        oos_metrics: BacktestMetrics,
        score: float,
        source_code: str,
        iteration: int,
    ) -> None:
        self.reset_stagnation()
        gen = self._generation + 1
        path = Path(self.config.strategy_output_dir) / f"{is_metrics.strategy_name}.py"
        self._best_snapshot = StrategySnapshot(
            strategy_name=is_metrics.strategy_name,
            source_path=path,
            source_code=source_code,
            composite_score=score,
            in_sample=is_metrics,
            out_of_sample=oos_metrics,
            generation=gen,
        )
        self._append(ExperimentRecord(
            experiment_id=self._make_id(iteration),
            iteration=iteration,
            timestamp=self._now_iso(),
            generation=gen,
            mutation=mutation,
            in_sample=is_metrics,
            out_of_sample=oos_metrics,
            composite_score=score,
            status="improvement",
        ))

    def record_regression(
        self,
        mutation: MutationRecord,
        is_metrics: BacktestMetrics,
        oos_metrics: BacktestMetrics | None,
        score: float,
        iteration: int,
        reason: str | None = None,
    ) -> None:
        self._append(ExperimentRecord(
            experiment_id=self._make_id(iteration),
            iteration=iteration,
            timestamp=self._now_iso(),
            generation=self._generation,
            mutation=mutation,
            in_sample=is_metrics,
            out_of_sample=oos_metrics,
            composite_score=score,
            status="regression",
            rejection_reason=reason,
        ))

    def record_rejection(
        self,
        mutation: MutationRecord,
        is_metrics: BacktestMetrics | None,
        reason: str,
        iteration: int,
    ) -> None:
        self._append(ExperimentRecord(
            experiment_id=self._make_id(iteration),
            iteration=iteration,
            timestamp=self._now_iso(),
            generation=self._generation,
            mutation=mutation,
            in_sample=is_metrics,
            composite_score=0.0,
            status="rejected",
            rejection_reason=reason,
        ))

    def record_error(self, mutation: MutationRecord, error_msg: str, iteration: int) -> None:
        self._append(ExperimentRecord(
            experiment_id=self._make_id(iteration),
            iteration=iteration,
            timestamp=self._now_iso(),
            generation=self._generation,
            mutation=mutation,
            composite_score=0.0,
            status="error",
            rejection_reason=error_msg,
        ))

    def current_best(self) -> StrategySnapshot | None:
        return self._best_snapshot

    def current_best_score(self) -> float:
        return self._best_snapshot.composite_score if self._best_snapshot else 0.0

    @property
    def stagnation_count(self) -> int:
        return self._stagnation_count

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def restart_history(self) -> list[dict[str, Any]]:
        return self._restart_history

    def record_stagnation(self) -> None:
        """Increment consecutive stagnation counter."""
        self._stagnation_count += 1

    def reset_stagnation(self) -> None:
        """Reset stagnation counter after an improvement."""
        self._stagnation_count = 0

    def record_restart(self, reason: str, direction: str = "") -> None:
        """Record a smart restart event."""
        self._restart_count += 1
        self._stagnation_count = 0
        self._restart_history.append({
            "restart_number": self._restart_count,
            "timestamp": self._now_iso(),
            "reason": reason,
            "direction": direction,
            "generation_at_restart": self._generation,
        })

    def history(self, last_n: int = 20) -> list[ExperimentRecord]:
        return self._records[-last_n:]

    @property
    def iteration_count(self) -> int:
        return len(self._records)

    def generate_report(self) -> str:
        improvements = sum(1 for r in self._records if r.status == "improvement")
        regressions = sum(1 for r in self._records if r.status == "regression")
        rejections = sum(1 for r in self._records if r.status == "rejected")
        errors = sum(1 for r in self._records if r.status == "error")

        lines = [
            "=== AutoResearch Evolution Report ===",
            f"Total iterations: {len(self._records)}",
            f"Improvements: {improvements}  Regressions: {regressions}  "
            f"Rejections: {rejections}  Errors: {errors}",
            f"Stagnation: {self._stagnation_count}  Restarts: {self._restart_count}",
            "",
        ]
        if self._best_snapshot:
            b = self._best_snapshot
            lines.extend([
                f"Best strategy: {b.strategy_name}",
                f"Generation: {b.generation}  Score: {b.composite_score:.4f}",
                f"IS  | Sharpe={b.in_sample.sharpe:.3f}  "
                f"PF={b.in_sample.profit_factor:.3f}  "
                f"DD={b.in_sample.max_drawdown_account:.1%}  "
                f"Trades={b.in_sample.total_trades}  "
                f"PnL={b.in_sample.profit_total_abs:.2f}",
                f"OOS | Sharpe={b.out_of_sample.sharpe:.3f}  "
                f"PnL={b.out_of_sample.profit_total_abs:.2f}",
            ])
        return "\n".join(lines)
