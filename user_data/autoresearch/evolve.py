"""
AutoResearch for Freqtrade - Main Evolution Loop

Entry point: python evolve.py --strategy <path> --config <freqtrade_config> [options]

Runs the autonomous strategy evolution loop:
  1. LLM proposes structural mutation (indicators, risk framework)
  2. Static check (AST validation)
  3. Apply mutation -> write strategy file
  4. Detect changed spaces (entry/exit/stoploss)
  5. Hyperopt parameter optimization (IS)
  6. In-sample backtest with optimized params + hard limits
  7. Out-of-sample backtest + validation
  8. Composite scoring
  9. Keep improvement or discard regression
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

from config import EvolutionConfig, load_evolution_config
from evaluator import compute_composite_score, evaluate_strategy, score_to_str
from hyperopt_runner import run_hyperopt
from mutator import Mutator
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
    )


def _combine_timeranges(first: str, second: str) -> str:
    """Combine two closed-open timeranges into one validation timerange."""
    first_parts = first.split("-")
    second_parts = second.split("-")
    if len(first_parts) != 2 or len(second_parts) != 2:
        return first
    return f"{first_parts[0]}-{second_parts[1]}"


def run_evolution(config: EvolutionConfig) -> None:  # noqa: C901
    start_time = time.time()
    max_seconds = config.max_walltime_hours * 3600

    logger.info("=== AutoResearch Strategy Evolution ===")
    logger.info(f"Strategy: {config.strategy_name}")
    logger.info(f"IS range: {config.in_sample_timerange}")
    logger.info(f"OOS range: {config.out_of_sample_timerange}")
    logger.info(f"Max iterations: {config.max_iterations}")

    is_baseline, oos_baseline, base_config, bt = establish_baseline(config)

    baseline_score = compute_composite_score(
        is_baseline, oos_baseline, is_baseline, config.score_weights, oos_baseline
    )
    logger.info(f"Baseline score: {baseline_score:.4f}")

    source_code = Path(config.strategy_path).read_text()

    tracker = ExperimentTracker(config)
    tracker.set_baseline(
        config.strategy_name, source_code, is_baseline, oos_baseline, baseline_score,
    )

    mutator = Mutator(config)

    for iteration in range(1, config.max_iterations + 1):
        elapsed = time.time() - start_time
        if elapsed > max_seconds:
            logger.info(f"Walltime limit: {elapsed / 3600:.1f}h")
            break

        logger.info(f"\n--- Iteration {iteration} (gen {tracker._generation}) ---")

        best = tracker.current_best()
        current_source = best.source_code if best else source_code

        # Phase 1: Propose mutation
        try:
            mutation, mutated_code = mutator.propose_mutation(
                current_source, is_baseline, best, tracker.history()
            )
        except Exception as e:
            logger.error(f"Mutation failed: {e}")
            tracker.record_error(MutationRecord("error", str(e)), str(e), iteration)
            time.sleep(5)
            continue

        logger.info(f"Mutation: {mutation.description[:100]}")

        # L1: Static check
        ok, reason = check_static(mutated_code)
        if not ok:
            logger.warning(f"Static check: {reason}")
            tracker.record_rejection(mutation, None, reason, iteration)
            continue

        # Phase 2: Apply mutation
        try:
            strat_name, strat_path = mutator.apply_mutation(
                mutated_code, iteration, config.strategy_output_dir
            )
        except Exception as e:
            logger.error(f"Apply failed: {e}")
            tracker.record_error(mutation, str(e), iteration)
            continue

        # Phase 3: Determine hyperopt spaces based on structural changes
        changed_spaces = mutator.detect_changed_spaces(current_source, mutated_code)
        spaces = list(config.hyperopt.spaces)
        if "buy" not in changed_spaces and "buy" in spaces:
            spaces.remove("buy")
        if "sell" not in changed_spaces and "sell" in spaces:
            spaces.remove("sell")
        if "stoploss" not in changed_spaces and "stoploss" in spaces:
            spaces.remove("stoploss")
        if not spaces:
            spaces = list(config.hyperopt.spaces)

        logger.info(
            f"Changed spaces: {changed_spaces} -> hyperopt will optimize: {spaces}"
        )

        # Phase 4: Hyperopt parameter optimization (IS)
        try:
            best_params = run_hyperopt(
                config, strat_name, config.in_sample_timerange, spaces, iteration
            )
            if best_params:
                logger.info(
                    f"Hyperopt found best params for spaces: {list(best_params.keys())}"
                )
        except Exception as e:
            logger.error(f"Hyperopt failed: {e}")
            tracker.record_error(mutation, str(e), iteration)
            mutator.cleanup_strategy(strat_path)
            time.sleep(5)
            continue

        # Phase 5: In-sample backtest (with hyperopt-optimized params)
        try:
            is_metrics, _ = evaluate_strategy(
                bt, base_config, strat_name, config.in_sample_timerange, iteration
            )
        except Exception as e:
            logger.error(f"IS backtest failed: {e}")
            tracker.record_error(mutation, str(e), iteration)
            mutator.cleanup_strategy(strat_path)
            time.sleep(5)
            continue

        # L2: Hard limits
        ok, reason = check_hard_limits(is_metrics, config.safety)
        if not ok:
            logger.warning(f"Hard limit: {reason}")
            tracker.record_rejection(mutation, is_metrics, reason, iteration)
            mutator.cleanup_strategy(strat_path)
            continue

        # Phase 6: OOS backtest (same hyperopt params)
        try:
            oos_metrics, _ = evaluate_strategy(
                bt, base_config, strat_name, config.out_of_sample_timerange, iteration
            )
        except Exception as e:
            logger.error(f"OOS backtest failed: {e}")
            tracker.record_error(mutation, str(e), iteration)
            mutator.cleanup_strategy(strat_path)
            time.sleep(5)
            continue

        # L3: OOS validation
        ok, reason = check_oos_validation(oos_metrics, is_metrics, config.safety)
        if not ok:
            logger.warning(f"OOS fail: {reason}")
            tracker.record_regression(
                mutation, is_metrics, oos_metrics, 0.0, iteration, reason
            )
            mutator.cleanup_strategy(strat_path)
            continue

        # L3b: For live-candidate selection, avoid promoting variants that look
        # cleaner on PF/DD but are materially weaker than the baseline.
        ok, reason = check_baseline_relative_validation(
            is_metrics, oos_metrics, is_baseline, oos_baseline, config.safety
        )
        if not ok:
            logger.warning(f"Baseline-relative fail: {reason}")
            tracker.record_regression(
                mutation, is_metrics, oos_metrics, 0.0, iteration, reason
            )
            mutator.cleanup_strategy(strat_path)
            continue

        # L4: Walk-forward validation across the full IS+OOS window.
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
            continue

        # Phase 7: Composite score
        score = compute_composite_score(
            is_metrics, oos_metrics, is_baseline, config.score_weights, oos_baseline
        )
        best_score = tracker.current_best_score()
        logger.info(score_to_str(score, is_metrics, oos_metrics))

        # Phase 8: Compare
        if score > best_score:
            logger.info(f"IMPROVEMENT! {best_score:.4f} -> {score:.4f}")
            tracker.record_improvement(
                mutation, is_metrics, oos_metrics, score, mutated_code, iteration
            )
        else:
            logger.info(f"Regression. Best={best_score:.4f} this={score:.4f}")
            tracker.record_regression(mutation, is_metrics, oos_metrics, score, iteration)
            mutator.cleanup_strategy(strat_path)

    report = tracker.generate_report()
    logger.info(f"\n{report}")
    logger.info("=== Evolution Complete ===")


def main() -> None:
    args = parse_args()
    config = build_config(args)
    run_evolution(config)


if __name__ == "__main__":
    main()
