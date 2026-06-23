# EfutureShort Strategy Extraction Design

## Goal

Extract the short (做空) trading logic from `EfutureIter.py` into an independent pure-short strategy named `EfutureShort`, saved as `user_data/strategies/EfutureShort.py`.

## Approach

Direct copy-and-prune: copy the full strategy, remove all long-specific code, keep only short entry/exit logic and shared parameters.

## Class Definition

```python
class EfutureShort(IStrategy):
    can_short = True
    # Base config identical to EfutureIter
```

## Parameters

### Retained (short entry, space="buy")

- `short_rsi_fast`, `short_rsi`, `short_sma15_ratio`, `short_cti`
- `short_24h_min_pct`, `short_24h_max_pct`, `short_volume_sma`, `short_adx`
- `short_tf_adx`, `short_tf_rsi_min`, `short_tf_rsi_max`
- `short_trend_strength`

### Retained (exit, space="sell")

- `sell_fastx`, `sell_trend_filter`, `sell_macd_profit`, `sell_bb_middle_profit`
- `time_exit_1_hours`, `time_exit_1_threshold`, `time_exit_2_hours`, `time_exit_2_threshold`
- `csl_mid_ratio`, `csl_late`
- `exit_adx_filter`, `exit_volatility_filter`

### Removed (all buy_* and long-only)

- `buy_rsi_fast`, `buy_rsi`, `buy_sma15_ratio`, `buy_cti`
- `buy_24h_min_pct`, `buy_24h_max_pct`, `buy_volume_sma`, `buy_adx`
- `buy_tf_adx`, `buy_tf_rsi_min`, `buy_tf_rsi_max`
- `buy_atr_ratio`, `buy_atr_sma_period`, `buy_trend_strength`

## populate_indicators

- Remove: `atr_sma` (only used by `buy_atr_sma_period`), `strong_uptrend`
- Simplify: `high_volatility` uses hardcoded period 30 instead of parameterized `buy_atr_sma_period`
- Keep: `sma_15`, `volume_sma`, `cti`, `rsi`, `rsi_fast`, `rsi_slow`, `24h_change_pct`, `fastk`, `cci`, `atr`, `adx`, `ema_50`, `ema_200`, `ema_*_uptrend`, `macd*`, `strong_downtrend`, `weak_trend`, `low_volatility`

## populate_entry_trend

- Keep: `short_mr` (mean-reversion) and `short_tf` (trend-following) entry signals
- Remove: all `long_mr` and `long_tf` logic, `enter_long` assignments

## custom_stoploss

- Remove: `else` (long) branch for `is_short` check
- Keep: short stoploss calculation only

## custom_exit

- Remove: `trend_exit_long`, `fastk_profit_sell`, `macd_reversal_long`, `cci_loss_sell`
- Simplify: `bearish` variable only needs short interpretation

## populate_exit_trend

- No change (empty implementation preserved)
