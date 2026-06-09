from pathlib import Path

import pytest

from freqtrade.resolvers.strategy_resolver import StrategyResolver


@pytest.fixture
def efuture_long_conf():
    return {
        "max_open_trades": 6,
        "stake_currency": "USDT",
        "stake_amount": "unlimited",
        "fiat_display_currency": "CNY",
        "timeframe": "5m",
        "dry_run": True,
        "cancel_open_orders_on_exit": False,
        "minimal_roi": {"0": 0.04},
        "dry_run_wallet": 1000,
        "tradable_balance_ratio": 0.99,
        "stoploss": -0.10,
        "unfilledtimeout": {"entry": 10, "exit": 30},
        "entry_pricing": {
            "price_last_balance": 0.0,
            "use_order_book": False,
            "order_book_top": 1,
            "check_depth_of_market": {"enabled": False, "bids_to_ask_delta": 1},
        },
        "exit_pricing": {
            "use_order_book": False,
            "order_book_top": 1,
        },
        "exchange": {
            "name": "binance",
            "key": "key",
            "enable_ws": False,
            "secret": "secret",
            "pair_whitelist": ["SOL/USDT:USDT", "DOT/USDT:USDT"],
            "pair_blacklist": [],
        },
        "pairlists": [{"method": "StaticPairList"}],
        "telegram": {
            "enabled": False,
            "token": "token",
            "chat_id": "1235",
            "notification_settings": {},
        },
        "datadir": Path("user_data/data"),
        "initial_state": "running",
        "db_url": "sqlite://",
        "user_data_dir": Path("user_data"),
        "verbosity": 3,
        "strategy_path": str(Path(__file__).parents[2] / "user_data" / "strategies"),
        "strategy": "EfutureLong",
        "disableparamexport": True,
        "internals": {},
        "export": "none",
        "dataformat_ohlcv": "feather",
        "dataformat_trades": "feather",
        "runmode": "dry_run",
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "candle_type_def": "futures",
        "original_config": {},
    }


def test_efuture_long_strategy_loads(efuture_long_conf):
    """Verify EfutureLong strategy can be loaded from user_data/strategies."""
    strategy = StrategyResolver.load_strategy(efuture_long_conf)
    assert strategy is not None
    assert strategy.__class__.__name__ == "EfutureLong"


def test_efuture_long_is_not_shortable(efuture_long_conf):
    """Verify EfutureLong has can_short=False."""
    strategy = StrategyResolver.load_strategy(efuture_long_conf)
    assert strategy.can_short is False


def test_efuture_long_optimal_defaults(efuture_long_conf):
    """Verify key parameters use optimal defaults from hyperopt JSON."""
    strategy = StrategyResolver.load_strategy(efuture_long_conf)

    # Buy params
    assert strategy.buy_rsi_fast.value == 40
    assert strategy.buy_rsi.value == 49
    assert strategy.buy_sma15_ratio.value == 0.98
    assert strategy.buy_cti.value == -0.34
    assert strategy.buy_24h_min_pct.value == -19.2
    assert strategy.buy_24h_max_pct.value == 182.2
    assert strategy.buy_volume_sma.value == 1.09
    assert strategy.buy_adx.value == 23
    assert strategy.buy_tf_adx.value == 37
    assert strategy.buy_tf_rsi_min.value == 44
    assert strategy.buy_tf_rsi_max.value == 56
    assert strategy.buy_trend_strength.value == 28

    # Sell params
    assert strategy.sell_fastx.value == 55
    assert strategy.sell_macd_profit.value == 0.02
    assert strategy.time_exit_1_hours.value == 7
    assert strategy.time_exit_1_threshold.value == -0.05
    assert strategy.time_exit_2_hours.value == 10
    assert strategy.time_exit_2_threshold.value == -0.10
    assert strategy.csl_mid_ratio.value == 1.0
    assert strategy.csl_late.value == -0.058
