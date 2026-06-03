"""
AutoResearch Hyperopt Runner

Programmatically executes freqtrade hyperopt for a strategy variant.
Best parameters are auto-exported to user_data/strategies/{strategy_name}.json
"""

import json
import logging
from pathlib import Path

from config import EvolutionConfig


logger = logging.getLogger(__name__)


def run_hyperopt(
    config: EvolutionConfig,
    strategy_name: str,
    timerange: str,
    spaces: list[str] | None = None,
    iteration: int = 0,
) -> dict:
    """
    Run freqtrade hyperopt for the given strategy and timerange.

    Returns best params dict, e.g.:
        {'buy': {'buy_rsi': 30}, 'sell': {'sell_rsi': 70}, ...}

    Parameters are automatically exported to:
        user_data/strategies/{strategy_name}.json
    """
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.hyperopt import Hyperopt

    config_path = Path(config.base_config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    hc = config.hyperopt
    spaces_to_use = list(spaces or hc.spaces)

    args = _build_hyperopt_args(config, strategy_name, timerange, spaces_to_use)

    hyperopt_config = setup_optimize_configuration(args, RunMode.HYPEROPT)

    # Inject custom loss weights/targets into config so custom loss can read them
    hyperopt_config["custom_loss_weights"] = hc.custom_loss_weights
    hyperopt_config["custom_loss_targets"] = hc.custom_loss_targets

    # Unique freqai identifier to avoid cache conflicts across iterations
    if "freqai" in hyperopt_config:
        hyperopt_config["freqai"]["identifier"] = f"autoresearch-hyperopt-{iteration:04d}"

    logger.info(
        f"Hyperopt {strategy_name}: epochs={hc.epochs} spaces={spaces_to_use} "
        f"loss={hc.loss_function} jobs={hc.jobs}"
    )
    logger.info(f"Hyperopt args: {args}")

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            from filelock import FileLock, Timeout

            lock = FileLock(Hyperopt.get_lock_filename(hyperopt_config))
            with lock.acquire(timeout=1):
                logging.getLogger("hyperopt.tpe").setLevel(logging.WARNING)
                logging.getLogger("filelock").setLevel(logging.WARNING)

                hyperopt = Hyperopt(hyperopt_config)
                hyperopt.start()

                best_epoch = hyperopt.current_best_epoch
                if best_epoch:
                    params = best_epoch.get("params_details", {})
                    loss = best_epoch.get("loss", 0)
                    logger.info(f"Hyperopt best loss={loss:.4f}")
                    return params
                logger.warning("Hyperopt completed but no best epoch found.")
                return {}

        except Timeout:
            logger.error("Another hyperopt instance is running. Cannot acquire lock.")
            raise RuntimeError("Hyperopt lock timeout") from None
        except Exception as e:
            err_msg = str(e)
            is_network_error = any(
                keyword in err_msg.lower()
                for keyword in [
                    "exchange",
                    "cannot connect",
                    "connection",
                    "timeout",
                    "unavailable",
                ]
            ) or "load markets" in err_msg.lower()
            if is_network_error and attempt < max_retries:
                wait_sec = 30 * attempt
                logger.warning(
                    f"Hyperopt network error (attempt {attempt}/{max_retries}): {e}. "
                    f"Retrying in {wait_sec}s..."
                )
                import time
                time.sleep(wait_sec)
                continue
            logger.error(f"Hyperopt failed: {e}")
            raise
    return {}


def _build_hyperopt_args(
    config: EvolutionConfig,
    strategy_name: str,
    timerange: str,
    spaces_to_use: list[str],
) -> dict:
    """Build freqtrade hyperopt args for a generated strategy candidate."""
    hc = config.hyperopt
    args: dict = {
        "config": [str(Path(config.base_config_path))],
        "strategy": strategy_name,
        "strategy_path": config.strategy_output_dir,
        "timeframe": config.timeframe,
        "pairs": list(config.pairs),
        "timerange": timerange,
        "epochs": hc.epochs,
        "spaces": spaces_to_use,
        "hyperopt_loss": hc.loss_function,
        "hyperopt_jobs": hc.jobs,
        "hyperopt_min_trades": hc.min_trades,
        "user_data_dir": str(Path(__file__).parent.parent.parent / "user_data"),
    }
    if hc.random_state is not None:
        args["hyperopt_random_state"] = hc.random_state
    if hc.early_stop > 0:
        args["early_stop"] = hc.early_stop
    if hc.analyze_per_epoch:
        args["analyze_per_epoch"] = True
    if hc.fee is not None:
        args["fee"] = hc.fee

    return args


def load_hyperopt_params(strategy_name: str, strategy_dir: str) -> dict:
    """Load exported hyperopt params from strategy's .json file."""
    params_file = Path(strategy_dir) / f"{strategy_name}.json"
    if not params_file.exists():
        return {}
    data = json.loads(params_file.read_text())
    return data.get("params", {})
