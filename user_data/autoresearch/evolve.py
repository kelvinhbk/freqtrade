"""
AutoResearch for Freqtrade - Main Evolution Loop

Entry point: python evolve.py --strategy <path> --config <freqtrade_config> [options]

Supports two evolution modes:
  - classic: Every iteration = LLM mutation + hyperopt (original behavior)
  - parameter_first: Repeatedly hyperopt until convergence, then LLM mutation

Both modes include: precheck, blacklist, dynamic epochs, smart restart.
"""

import multiprocessing


try:
    multiprocessing.set_start_method("spawn")
except RuntimeError:
    pass

import argparse
import logging
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

from blacklist import BlacklistRule, detect_failure_pattern, load_blacklist
from config import EvolutionConfig, load_evolution_config
from epochs_calculator import calculate_dynamic_epochs
from evaluator import compute_composite_score, evaluate_strategy, score_to_str
from hyperopt_runner import run_hyperopt
from mutator import Mutator
from precheck import run_precheck
from prepare import establish_baseline
from safety import (
    check_baseline_relative_validation,
    check_hard_limits,
    check_oos_validation,
    check_static,
    check_walk_forward,
)
from tracker import ExperimentTracker, MutationRecord


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evolve")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AutoResearch Strategy Evolution")
    parser.add_argument("--strategy", required=True, help="Path to base strategy .py file")
    parser.add_argument("--freqtrade-config", required=True, help="Path to freqtrade config .json")
    parser.add_argument("--evolution-config", default=None, help="Path to evolution config .json")
    parser.add_argument(
        "--is-range", default=None, help="In-sample timerange (e.g. 20240701-20250101)"
    )
    parser.add_argument("--oos-range", default=None, help="OOS timerange (e.g. 20250101-20250501)")
    parser.add_argument("--max-iter", type=int, default=None, help="Max iterations")
    parser.add_argument("--max-hours", type=float, default=None, help="Max walltime hours")
    parser.add_argument(
        "--no-confirm", action="store_true",
        help="Skip pre-evolution confirmation prompt",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> EvolutionConfig:
    base = load_evolution_config(args.evolution_config)
    if not any([
        args.strategy, args.freqtrade_config, args.is_range,
        args.oos_range, args.max_iter, args.max_hours,
    ]):
        return base

    strategy_path = str(Path(args.strategy).resolve()) if args.strategy else base.strategy_path
    base_config_path = (
        str(Path(args.freqtrade_config).resolve())
        if args.freqtrade_config
        else base.base_config_path
    )

    return EvolutionConfig(
        llm=base.llm,
        safety=base.safety,
        score_weights=base.score_weights,
        hyperopt=base.hyperopt,
        max_iterations=args.max_iter or base.max_iterations,
        max_walltime_hours=args.max_hours or base.max_walltime_hours,
        in_sample_timerange=args.is_range or base.in_sample_timerange,
        out_of_sample_timerange=args.oos_range or base.out_of_sample_timerange,
        strategy_path=strategy_path,
        base_config_path=base_config_path,
        pairs=base.pairs,
        timeframe=base.timeframe,
        results_file=base.results_file,
        strategy_output_dir=base.strategy_output_dir,
        mutation_focus=base.mutation_focus,
        mutation_constraints=base.mutation_constraints,
        evolution_mode=base.evolution_mode,
        param_convergence_rounds=base.param_convergence_rounds,
        param_improvement_threshold=base.param_improvement_threshold,
        stagnation_limit=base.stagnation_limit,
        max_restarts=base.max_restarts,
        db_path=base.db_path,
        blacklist_dir=base.blacklist_dir,
        epochs_floor=base.epochs_floor,
        epochs_ceiling=base.epochs_ceiling,
        global_blacklist_threshold=base.global_blacklist_threshold,
    )


def _combine_timeranges(first: str, second: str) -> str:
    """Combine two closed-open timeranges into one validation timerange."""
    first_parts = first.split("-")
    second_parts = second.split("-")
    if len(first_parts) != 2 or len(second_parts) != 2:
        return first
    return f"{first_parts[0]}-{second_parts[1]}"


def run_evolution(config: EvolutionConfig, confirm: bool = True) -> None:  # noqa: C901
    start_time = time.time()
    max_seconds = config.max_walltime_hours * 3600

    logger.info("=== AutoResearch Strategy Evolution ===")
    logger.info(f"Strategy: {config.strategy_name}")
    logger.info(f"Mode: {config.evolution_mode}")
    logger.info(f"IS range: {config.in_sample_timerange}")
    logger.info(f"OOS range: {config.out_of_sample_timerange}")
    logger.info(f"Max iterations: {config.max_iterations}")

    # Phase 0: Establish baseline
    is_baseline, oos_baseline, base_config, bt = establish_baseline(config)

    baseline_score = compute_composite_score(
        is_baseline, oos_baseline, is_baseline, config.score_weights, oos_baseline
    )
    logger.info(f"Baseline score: {baseline_score:.4f}")

    source_code = Path(config.strategy_path).read_text()

    # Pre-evolution check (displays info and optionally waits for confirmation)
    blacklist_rules = load_blacklist(config)
    confirmed = run_precheck(
        config, is_baseline, oos_baseline, blacklist_rules, source_code,
        confirm=confirm,
    )
    if not confirmed:
        logger.info("Evolution cancelled by user.")
        return

    tracker = ExperimentTracker(config)
    tracker.set_baseline(
        config.strategy_name, source_code, is_baseline, oos_baseline, baseline_score,
    )

    mutator = Mutator(config)

    # Three-phase state tracking for parameter_first mode
    param_phase_count = 0  # Consecutive parameter-only rounds
    iteration = 0

    while iteration < config.max_iterations:
        iteration += 1
        elapsed = time.time() - start_time
        if elapsed > max_seconds:
            logger.info(f"Walltime limit: {elapsed / 3600:.1f}h")
            break

        # Check stagnation -> smart restart
        if tracker.stagnation_count >= config.stagnation_limit:
            if tracker.restart_count >= config.max_restarts:
                logger.info(
                    f"Max restarts ({config.max_restarts}) reached. Stopping."
                )
                break
            _handle_stagnation(config, tracker, is_baseline, oos_baseline)
            param_phase_count = 0
            continue

        logger.info(
            f"\n--- Iteration {iteration} "
            f"(gen {tracker._generation}, "
            f"stag={tracker.stagnation_count}, "
            f"restarts={tracker.restart_count}) ---"
        )

        if config.evolution_mode == "parameter_first":
            # Phase 1: Parameter-only optimization (skip LLM mutation)
            if param_phase_count < config.param_convergence_rounds:
                improved = _run_parameter_phase(
                    config, tracker, mutator, bt, base_config,
                    is_baseline, oos_baseline, iteration,
                )
                if improved:
                    param_phase_count = 0
                else:
                    param_phase_count += 1
                    tracker.record_stagnation()
                continue
            # Converged -> fall through to LLM mutation
            param_phase_count = 0
            logger.info("Parameter phase converged. Switching to LLM mutation.")

        # Phase 2 (classic mode always hits this; parameter_first after convergence)
        improved = _run_mutation_phase(
            config, tracker, mutator, bt, base_config,
            is_baseline, oos_baseline, iteration, source_code,
        )
        if not improved:
            tracker.record_stagnation()

    report = tracker.generate_report()
    logger.info(f"\n{report}")
    logger.info("=== Evolution Complete ===")


def _run_parameter_phase(
    config: EvolutionConfig,
    tracker: ExperimentTracker,
    mutator: Mutator,
    bt,
    base_config: dict,
    is_baseline,
    oos_baseline,
    iteration: int,
) -> bool:
    """Parameter-only optimization: hyperopt with current strategy code.

    Returns True if a new best score was achieved.
    """
    best = tracker.current_best()
    current_source = best.source_code if best else ""
    strat_name = best.strategy_name if best else config.strategy_name

    logger.info(f"[Param Phase] Re-optimizing params for {strat_name}")

    # Dynamic epochs
    spaces = list(config.hyperopt.spaces)
    epochs = calculate_dynamic_epochs(current_source, spaces, config)

    try:
        best_params = run_hyperopt(
            config, strat_name, config.in_sample_timerange, spaces,
            iteration, epochs_override=epochs,
        )
        if best_params:
            logger.info(
                f"Hyperopt found params: {list(best_params.keys())}"
            )
    except Exception as e:
        logger.error(f"Param phase hyperopt failed: {e}")
        time.sleep(5)
        return False

    # Evaluate with optimized params
    try:
        is_metrics, _ = evaluate_strategy(
            bt, base_config, strat_name, config.in_sample_timerange, iteration
        )
    except Exception as e:
        logger.error(f"Param phase IS backtest failed: {e}")
        time.sleep(5)
        return False

    ok, reason = check_hard_limits(is_metrics, config.safety)
    if not ok:
        logger.warning(f"Param phase hard limit: {reason}")
        return False

    try:
        oos_metrics, _ = evaluate_strategy(
            bt, base_config, strat_name, config.out_of_sample_timerange, iteration
        )
    except Exception as e:
        logger.error(f"Param phase OOS backtest failed: {e}")
        time.sleep(5)
        return False

    ok, reason = check_oos_validation(oos_metrics, is_metrics, config.safety)
    if not ok:
        logger.warning(f"Param phase OOS fail: {reason}")
        return False

    ok, reason = check_baseline_relative_validation(
        is_metrics, oos_metrics, is_baseline, oos_baseline, config.safety
    )
    if not ok:
        logger.warning(f"Param phase baseline-relative fail: {reason}")
        return False

    score = compute_composite_score(
        is_metrics, oos_metrics, is_baseline, config.score_weights, oos_baseline
    )
    best_score = tracker.current_best_score()
    logger.info(score_to_str(score, is_metrics, oos_metrics))

    if score > best_score * (1 + config.param_improvement_threshold):
        logger.info(f"PARAM IMPROVEMENT! {best_score:.4f} -> {score:.4f}")
        mutation = MutationRecord("parameter_optimization", "Parameter-only hyperopt")
        tracker.record_improvement(
            mutation, is_metrics, oos_metrics, score, current_source, iteration
        )
        return True

    logger.info(
        f"Param phase no significant improvement. "
        f"Best={best_score:.4f} this={score:.4f}"
    )
    return False


def _run_mutation_phase(
    config: EvolutionConfig,
    tracker: ExperimentTracker,
    mutator: Mutator,
    bt,
    base_config: dict,
    is_baseline,
    oos_baseline,
    iteration: int,
    original_source: str,
) -> bool:
    """LLM structural mutation + hyperopt + evaluation.

    Returns True if a new best score was achieved.
    """
    best = tracker.current_best()
    current_source = best.source_code if best else original_source

    # Propose mutation (blacklist injected inside mutator)
    try:
        mutation, mutated_code = mutator.propose_mutation(
            current_source, is_baseline, best, tracker.history()
        )
    except Exception as e:
        logger.error(f"Mutation failed: {e}")
        tracker.record_error(MutationRecord("error", str(e)), str(e), iteration)
        time.sleep(5)
        return False

    logger.info(f"Mutation: {mutation.description[:100]}")

    # L1: Static check
    ok, reason = check_static(mutated_code)
    if not ok:
        logger.warning(f"Static check: {reason}")
        tracker.record_rejection(mutation, None, reason, iteration)
        return False

    # Apply mutation
    try:
        strat_name, strat_path = mutator.apply_mutation(
            mutated_code, iteration, config.strategy_output_dir
        )
    except Exception as e:
        logger.error(f"Apply failed: {e}")
        tracker.record_error(mutation, str(e), iteration)
        return False

    # Determine hyperopt spaces based on structural changes
    changed_spaces = mutator.detect_changed_spaces(current_source, mutated_code)
    spaces = list(config.hyperopt.spaces)
    for unchanged in ("buy", "sell", "stoploss"):
        if unchanged not in changed_spaces and unchanged in spaces:
            spaces.remove(unchanged)
    if not spaces:
        spaces = list(config.hyperopt.spaces)

    logger.info(f"Changed spaces: {changed_spaces} -> optimizing: {spaces}")

    # Dynamic epochs
    epochs = calculate_dynamic_epochs(mutated_code, spaces, config)

    # Hyperopt
    try:
        best_params = run_hyperopt(
            config, strat_name, config.in_sample_timerange, spaces,
            iteration, epochs_override=epochs,
        )
        if best_params:
            logger.info(f"Hyperopt params: {list(best_params.keys())}")
    except Exception as e:
        logger.error(f"Hyperopt failed: {e}")
        tracker.record_error(mutation, str(e), iteration)
        mutator.cleanup_strategy(strat_path)
        time.sleep(5)
        return False

    # IS backtest
    try:
        is_metrics, _ = evaluate_strategy(
            bt, base_config, strat_name, config.in_sample_timerange, iteration
        )
    except Exception as e:
        logger.error(f"IS backtest failed: {e}")
        tracker.record_error(mutation, str(e), iteration)
        mutator.cleanup_strategy(strat_path)
        time.sleep(5)
        return False

    # L2: Hard limits
    ok, reason = check_hard_limits(is_metrics, config.safety)
    if not ok:
        logger.warning(f"Hard limit: {reason}")
        tracker.record_rejection(mutation, is_metrics, reason, iteration)
        mutator.cleanup_strategy(strat_path)
        return False

    # OOS backtest
    try:
        oos_metrics, _ = evaluate_strategy(
            bt, base_config, strat_name, config.out_of_sample_timerange, iteration
        )
    except Exception as e:
        logger.error(f"OOS backtest failed: {e}")
        tracker.record_error(mutation, str(e), iteration)
        mutator.cleanup_strategy(strat_path)
        time.sleep(5)
        return False

    # L3: OOS validation
    ok, reason = check_oos_validation(oos_metrics, is_metrics, config.safety)
    if not ok:
        logger.warning(f"OOS fail: {reason}")
        tracker.record_regression(
            mutation, is_metrics, oos_metrics, 0.0, iteration, reason
        )
        mutator.cleanup_strategy(strat_path)
        return False

    # L3b: Baseline-relative validation
    ok, reason = check_baseline_relative_validation(
        is_metrics, oos_metrics, is_baseline, oos_baseline, config.safety
    )
    if not ok:
        logger.warning(f"Baseline-relative fail: {reason}")
        tracker.record_regression(
            mutation, is_metrics, oos_metrics, 0.0, iteration, reason
        )
        mutator.cleanup_strategy(strat_path)
        return False

    # L4: Walk-forward validation
    validation_timerange = _combine_timeranges(
        config.in_sample_timerange, config.out_of_sample_timerange
    )
    ok, reason = check_walk_forward(
        bt, base_config, strat_name, validation_timerange, config.safety, iteration
    )
    if not ok:
        logger.warning(f"Walk-forward fail: {reason}")
        tracker.record_regression(
            mutation, is_metrics, oos_metrics, 0.0, iteration, reason
        )
        mutator.cleanup_strategy(strat_path)
        return False

    # Composite score
    score = compute_composite_score(
        is_metrics, oos_metrics, is_baseline, config.score_weights, oos_baseline
    )
    best_score = tracker.current_best_score()
    logger.info(score_to_str(score, is_metrics, oos_metrics))

    if score > best_score:
        logger.info(f"IMPROVEMENT! {best_score:.4f} -> {score:.4f}")
        tracker.record_improvement(
            mutation, is_metrics, oos_metrics, score, mutated_code, iteration
        )
        return True

    logger.info(f"Regression. Best={best_score:.4f} this={score:.4f}")
    tracker.record_regression(mutation, is_metrics, oos_metrics, score, iteration)
    mutator.cleanup_strategy(strat_path)
    return False


def _handle_stagnation(
    config: EvolutionConfig,
    tracker: ExperimentTracker,
    is_baseline,
    oos_baseline,
) -> None:
    """Smart restart: detect failure patterns, update blacklist, adjust direction."""
    logger.info(
        f"=== Stagnation detected ({tracker.stagnation_count} rounds). "
        f"Initiating smart restart #{tracker.restart_count + 1} ==="
    )

    # Step 1: Detect failure patterns and update blacklist
    history = tracker.history(30)
    new_rule = detect_failure_pattern(history)
    if new_rule:
        from blacklist import _next_rule_id
        existing_rules = load_blacklist(config)
        rule_id = _next_rule_id(existing_rules)
        rule = BlacklistRule(
            id=rule_id,
            pattern=new_rule.pattern,
            description=new_rule.description,
            reason=new_rule.reason,
            created_at=new_rule.created_at,
            failed_count=new_rule.failed_count,
            source_experiments=new_rule.source_experiments,
        )
        from blacklist import add_rule
        add_rule(config, rule)
        logger.info(f"Auto-added blacklist rule: {rule_id}")

    # Step 2: Determine restart direction
    direction = _determine_restart_direction(is_baseline, oos_baseline)
    reason = f"Stagnation after {tracker.stagnation_count} rounds"

    # Step 3: Record restart
    tracker.record_restart(reason, direction)
    logger.info(f"Restart direction: {direction or 'general'}")


def _determine_restart_direction(
    is_baseline,
    oos_baseline,
) -> str:
    """Analyze baseline metrics to suggest next mutation direction."""
    if oos_baseline.total_trades < 30:
        return "expand_signals"
    if oos_baseline.profit_total_abs <= 0 and is_baseline.profit_total_abs <= 0:
        return "fundamental_rethink"
    if oos_baseline.max_drawdown_account > 0.20:
        return "risk_management"
    return "general"


def main() -> None:
    args = parse_args()
    config = build_config(args)
    run_evolution(config, confirm=not args.no_confirm)


if __name__ == "__main__":
    main()
