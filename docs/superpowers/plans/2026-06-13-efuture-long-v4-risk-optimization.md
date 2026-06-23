# EfutureLong_v4 风控优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 EfutureLong.py 创建 EfutureLong_v4.py，实现三重保护层（trailing 止盈 + 兜底 ROI + 信号退出），将最大单笔亏损从 ~31% 降到 <=15%。

**Architecture:** 在现有 custom_stoploss 框架上增加利润依赖的 trailing 止损（利润越高止损越紧），收紧硬止损从 -0.10 到 -0.05，增加 minimal_roi 兜底层，简化 custom_exit 移除冗余的 fastk 退出，增强 protections 机制。

**Tech Stack:** Python, freqtrade strategy framework, talib, pandas_ta

**Design Spec:** `docs/superpowers/specs/2026-06-12-efuture-long-v4-risk-optimization-design.md`

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `user_data/strategies/EfutureLong_v4.py` | Create | 新策略文件，所有变更集中在此 |

不修改任何现有文件。EfutureLong.py 保持不动。

---

### Task 1: 复制并重命名基础文件

**Files:**
- Create: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 复制源文件**

```bash
cp user_data/strategies/EfutureLong.py user_data/strategies/EfutureLong_v4.py
```

- [ ] **Step 2: 重命名类名**

在 `EfutureLong_v4.py` 中，将 `class EfutureLong(IStrategy):` 改为 `class EfutureLong_v4(IStrategy):`

- [ ] **Step 3: 验证文件可被 freqtrade 识别**

```bash
python -c "from user_data.strategies.EfutureLong_v4 import EfutureLong_v4; print(EfutureLong_v4.__name__)"
```

Expected: `EfutureLong_v4`

- [ ] **Step 4: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): add EfutureLong_v4 base from EfutureLong"
```

---

### Task 2: 修改硬止损参数

**Files:**
- Modify: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 修改 stoploss**

将：
```python
    stoploss = -0.10
```
改为：
```python
    stoploss = -0.05
```

- [ ] **Step 2: 修改 csl_initial**

将：
```python
    csl_initial = -0.10
```
改为：
```python
    csl_initial = -0.05
```

- [ ] **Step 3: 验证策略仍可加载**

```bash
python -c "from user_data.strategies.EfutureLong_v4 import EfutureLong_v4; s = EfutureLong_v4(); print(f'stoploss={s.stoploss}, csl_initial={s.csl_initial}')"
```

Expected: `stoploss=-0.05, csl_initial=-0.05`

- [ ] **Step 4: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): tighten hard stoploss from -0.10 to -0.05"
```

---

### Task 3: 修改 minimal_roi

**Files:**
- Modify: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 替换 minimal_roi**

将：
```python
    minimal_roi = {
        "0": 0.137,
        "20": 0.097,
        "57": 0.034,
        "169": 0,
    }
```
改为：
```python
    minimal_roi = {
        "0": 0.25,
        "10": 0.15,
        "30": 0.10,
        "60": 0.07,
        "120": 0.04,
        "240": 0.02,
        "480": 0.01,
    }
```

- [ ] **Step 2: 验证**

```bash
python -c "from user_data.strategies.EfutureLong_v4 import EfutureLong_v4; s = EfutureLong_v4(); print(s.minimal_roi)"
```

Expected: `{'0': 0.25, '10': 0.15, '30': 0.1, '60': 0.07, '120': 0.04, '240': 0.02, '480': 0.01}`

- [ ] **Step 3: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): add 7-tier minimal_roi as profit safety net"
```

---

### Task 4: 新增 trailing 参数定义并替换时间退出参数

**Files:**
- Modify: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 移除 sell_fastx 参数**

删除这一行：
```python
    sell_fastx = IntParameter(50, 100, default=55, space="sell", optimize=True)
```

- [ ] **Step 2: 新增 trailing 参数**

在 `# --- Custom stoploss parameters ---` 之前，插入：
```python
    # --- Trailing stop parameters ---
    trail_tier1_profit = DecimalParameter(
        0.02, 0.08, default=0.03, decimals=3, space="sell", optimize=True
    )
    trail_tier1_distance = DecimalParameter(
        0.01, 0.04, default=0.03, decimals=3, space="sell", optimize=True
    )
    trail_tier2_profit = DecimalParameter(
        0.06, 0.15, default=0.08, decimals=3, space="sell", optimize=True
    )
    trail_tier2_distance = DecimalParameter(
        0.005, 0.03, default=0.02, decimals=3, space="sell", optimize=True
    )
    trail_tier3_profit = DecimalParameter(
        0.10, 0.25, default=0.15, decimals=3, space="sell", optimize=True
    )
    trail_tier3_distance = DecimalParameter(
        0.003, 0.02, default=0.01, decimals=3, space="sell", optimize=True
    )
```

- [ ] **Step 3: 替换时间退出参数**

将旧的时间退出参数：
```python
    # --- Time exit parameters ---
    time_exit_1_hours = IntParameter(4, 12, default=7, space="sell", optimize=True)
    time_exit_1_threshold = DecimalParameter(
        -0.08, -0.02, default=-0.05, decimals=3, space="sell", optimize=True
    )
    time_exit_2_hours = IntParameter(8, 16, default=10, space="sell", optimize=True)
    time_exit_2_threshold = DecimalParameter(
        -0.15, -0.05, default=-0.10, decimals=3, space="sell", optimize=True
    )
```
改为：
```python
    # --- Time exit parameters ---
    time_exit_1_hours = IntParameter(3, 6, default=4, space="sell", optimize=True)
    time_exit_1_threshold = DecimalParameter(
        -0.05, -0.01, default=-0.02, decimals=3, space="sell", optimize=True
    )
    time_exit_2_hours = IntParameter(6, 9, default=7, space="sell", optimize=True)
    time_exit_2_threshold = DecimalParameter(
        -0.03, -0.005, default=-0.01, decimals=3, space="sell", optimize=True
    )
    time_exit_3_hours = IntParameter(9, 14, default=10, space="sell", optimize=True)
```

- [ ] **Step 4: 验证参数加载**

```bash
python -c "
from user_data.strategies.EfutureLong_v4 import EfutureLong_v4
s = EfutureLong_v4()
print(f'trail_tier1_profit={s.trail_tier1_profit.value}')
print(f'trail_tier2_profit={s.trail_tier2_profit.value}')
print(f'trail_tier3_profit={s.trail_tier3_profit.value}')
print(f'time_exit_1_hours={s.time_exit_1_hours.value}')
print(f'time_exit_3_hours={s.time_exit_3_hours.value}')
"
```

Expected:
```
trail_tier1_profit=0.03
trail_tier2_profit=0.08
trail_tier3_profit=0.15
time_exit_1_hours=4
time_exit_3_hours=10
```

- [ ] **Step 5: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): add trailing stop parameters and revise time exit params"
```

---

### Task 5: 重写 custom_stoploss

**Files:**
- Modify: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 替换 custom_stoploss 方法**

将整个 `custom_stoploss` 方法替换为：

```python
    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        # Layer 1: Profit-based trailing (highest priority)
        # Profit越高 -> trailing距离越小 -> 止损越紧
        if current_profit > self.trail_tier3_profit.value:
            return -self.trail_tier3_distance.value
        if current_profit > self.trail_tier2_profit.value:
            return -self.trail_tier2_distance.value
        if current_profit > self.trail_tier1_profit.value:
            return -self.trail_tier1_distance.value

        # Layer 2: Time + ATR dynamic stoploss
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if len(dataframe) < 1:
            return self.stoploss

        current_candle = dataframe.iloc[-1]
        atr = current_candle["atr"]
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 60

        atr_pct = max(
            -(atr / current_rate) * self.csl_mid_ratio.value, self.stoploss
        )

        if elapsed < 30:
            return self.csl_initial
        elif elapsed < 240:
            return max(-0.05, atr_pct)
        else:
            return max(self.csl_late.value, atr_pct)
```

- [ ] **Step 2: 验证策略加载无报错**

```bash
python -c "from user_data.strategies.EfutureLong_v4 import EfutureLong_v4; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): implement profit-based trailing stoploss with 3 tiers"
```

---

### Task 6: 简化 custom_exit

**Files:**
- Modify: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 替换 custom_exit 方法**

将整个 `custom_exit` 方法替换为：

```python
    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if len(dataframe) < 1:
            return None
        current_candle = dataframe.iloc[-1]

        # Check trend direction using pre-computed columns
        bearish = not current_candle["ema_50_uptrend"] and not current_candle["ema_200_uptrend"]

        # Layer 1: Trend filter exit
        if (
            current_candle["weak_trend"]
            and current_candle["low_volatility"]
            and current_candle["ema_50_uptrend"]
        ):
            return "trend_exit_long"

        # Layer 2: MACD reversal exit (profit > threshold AND MACD cross down)
        if (
            current_profit > self.sell_macd_profit.value
            and current_candle["macd_cross_down"]
        ):
            return "macd_reversal_long"

        # Layer 3: Loss mitigation exit
        if -0.03 < current_profit < 0 and current_candle["cci"] > 80 and bearish:
            return "cci_loss_sell"

        # Layer 4: Time-based loss cuts (3 tiers)
        if (
            current_time - timedelta(hours=self.time_exit_1_hours.value) > trade.open_date_utc
            and current_profit >= self.time_exit_1_threshold.value
        ):
            return "time_loss_1"

        if (
            current_time - timedelta(hours=self.time_exit_2_hours.value) > trade.open_date_utc
            and current_profit >= self.time_exit_2_threshold.value
        ):
            return "time_loss_2"

        # Layer 5: Force exit after max holding time
        if current_time - timedelta(hours=self.time_exit_3_hours.value) > trade.open_date_utc:
            return "time_force_exit"

        return None
```

关键变更：
- 移除了 `fastk > sell_fastx` 的利润退出（被 trailing 替代）
- 新增 `time_force_exit`（持仓超过 time_exit_3_hours 无条件退出）

- [ ] **Step 2: 验证策略加载无报错**

```bash
python -c "from user_data.strategies.EfutureLong_v4 import EfutureLong_v4; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): simplify custom_exit - remove fastk, add force exit tier"
```

---

### Task 7: 更新 protections

**Files:**
- Modify: `user_data/strategies/EfutureLong_v4.py`

- [ ] **Step 1: 替换 protections 属性**

将：
```python
    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 48,
            }
        ]
```
改为：
```python
    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 48,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 60,
                "trade_limit": 2,
                "stop_duration_candles": 40,
            },
            {
                "method": "MaxDrawdown",
                "trade_limit": 20,
                "stop_duration_candles": 80,
                "max_allowed_drawdown": -0.15,
            },
        ]
```

- [ ] **Step 2: 验证**

```bash
python -c "
from user_data.strategies.EfutureLong_v4 import EfutureLong_v4
s = EfutureLong_v4()
prots = s.protections
print(f'{len(prots)} protections')
for p in prots:
    print(f'  {p[\"method\"]}')
"
```

Expected:
```
3 protections
  CooldownPeriod
  StoplossGuard
  MaxDrawdown
```

- [ ] **Step 3: Commit**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): add StoplossGuard and MaxDrawdown protections"
```

---

### Task 8: 最终验证

- [ ] **Step 1: 完整策略加载测试**

```bash
python -c "
from user_data.strategies.EfutureLong_v4 import EfutureLong_v4
s = EfutureLong_v4()
print(f'Class: {s.__class__.__name__}')
print(f'stoploss: {s.stoploss}')
print(f'csl_initial: {s.csl_initial}')
print(f'minimal_roi: {s.minimal_roi}')
print(f'protections: {len(s.protections)}')
print(f'trail_tier1: profit={s.trail_tier1_profit.value}, dist={s.trail_tier1_distance.value}')
print(f'trail_tier2: profit={s.trail_tier2_profit.value}, dist={s.trail_tier2_distance.value}')
print(f'trail_tier3: profit={s.trail_tier3_profit.value}, dist={s.trail_tier3_distance.value}')
print(f'time_exit_1: {s.time_exit_1_hours.value}h @ {s.time_exit_1_threshold.value}')
print(f'time_exit_2: {s.time_exit_2_hours.value}h @ {s.time_exit_2_threshold.value}')
print(f'time_exit_3: {s.time_exit_3_hours.value}h (force)')
print('ALL OK')
"
```

Expected output 应显示所有参数值与设计文档一致。

- [ ] **Step 2: 语法检查**

```bash
python -m py_compile user_data/strategies/EfutureLong_v4.py && echo "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit (如有遗漏修正)**

```bash
git add user_data/strategies/EfutureLong_v4.py
git commit -m "feat(strategy): EfutureLong_v4 - risk optimization complete"
```
