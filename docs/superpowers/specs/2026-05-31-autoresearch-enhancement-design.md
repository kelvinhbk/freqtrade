# AutoResearch Enhancement Design

Date: 2026-05-31
Status: Approved
Approach: Modular Enhancement (方案 A)

## Background

AutoResearch 进化系统在 macd_v4 策略上运行了 5 轮共 40+ 次迭代，未能超越基线 0.5000。核心问题：

1. 基线策略本身亏损（IS -11.58%, OOS -18.77%），进化空间有限
2. LLM 反复犯同样错误（添加趋势过滤器导致 OOS 交易崩溃）
3. 固定 500 epochs 浪费时间，参数少时过多
4. 每轮都 LLM 变异 + hyperopt，即使参数还没优化到极限
5. 多空表现差异大（多头 -17.61%, 空头 +6.03%）但未针对性优化
6. 缺少进化前的策略健康检查

## Design Overview

在现有架构上新增 4 个模块、修改 5 个模块，实现 10 项优化。

### 新增文件

```
user_data/autoresearch/
├── precheck.py           # 进化前检查、基线健康评估
├── blacklist.py          # 黑名单 CRUD、失败模式检测、prompt 注入
├── epochs_calculator.py  # 动态 epochs、受影响空间分析、多空预算分配
├── blacklists/           # 黑名单存储目录
│   ├── global.json       # 跨策略通用教训（用户确认后才写入）
│   ├── macd_v4.json      # macd_v4 专属黑名单
│   └── ENEW.json         # ENEW 专属黑名单
```

### 修改文件

```
user_data/autoresearch/
├── evolve.py             # 集成三阶段循环、precheck、智能重启
├── mutator.py            # 注入黑名单到 prompt、新增 focus 参数
├── hyperopt_runner.py    # 接受动态 epochs、锁定参数空间
├── config.py             # 新增进化模式、收敛、停滞等配置字段
├── tracker.py            # 记录重启历史、停滞计数
```

---

## Part 1: Evolution Mode Refactor (三阶段进化循环)

### Current Problem

每轮都是 LLM 变异 -> hyperopt，即使参数还没优化到极限就浪费 LLM 调用。

### New Design: Three-Phase Evolution Loop

```
Phase 1: Parameter-Only Phase
  ├── Repeatedly hyperopt with current strategy code
  ├── Only optimize affected parameter spaces
  ├── Detect convergence: N consecutive rounds with < threshold improvement
  └── Converged -> enter Phase 2

Phase 2: LLM Structural Mutation
  ├── Inject blacklist lessons
  ├── Long/short asymmetry analysis -> guide mutation direction
  ├── Generate mutated code
  └── After mutation -> back to Phase 1

Phase 3: Smart Restart (when Phase 1+2 consecutive N rounds no improvement)
  ├── Record failure patterns to blacklist
  ├── Adjust safety thresholds (temporarily relax)
  └── Reset evolution direction
```

### Configuration

```json
{
  "evolution_mode": "parameter_first",
  "param_convergence_rounds": 3,
  "param_improvement_threshold": 0.05,
  "stagnation_limit": 10
}
```

| Config | Purpose | Example |
|--------|---------|---------|
| `evolution_mode` | `parameter_first` = params then mutation; `classic` = original every-round mutation | `parameter_first` |
| `param_convergence_rounds` | How many consecutive rounds without significant improvement before switching to LLM mutation | 3 |
| `param_improvement_threshold` | Minimum improvement ratio to count as "significant" | 0.05 (5%) |
| `stagnation_limit` | Total consecutive rounds without any improvement before triggering smart restart | 10 |

---

## Part 2: Dynamic Epochs + Incremental Parameter Locking

### Current Problem

Fixed 500 epochs. Too many for few parameters, too few for many.

### Epochs Calculation Formula

```
epochs = max(200, param_count * range_factor * 50)
```

- `param_count`: number of parameters to optimize in current round
- `range_factor`: parameter range size factor (0.1-1.0 range = 1.0, narrow range = 0.5)
- Floor: 200, Ceiling: configurable

### Incremental Parameter Locking

After each mutation:
1. Analyze which `space` (buy/sell/roi/stoploss) was modified
2. Lock parameters in untouched spaces to previous hyperopt best values
3. Only optimize parameters in modified spaces

### Long/Short Asymmetric Budget

Before hyperopt:
1. Analyze IS backtest long vs short performance
2. Allocate more epochs to the weaker direction (e.g., 70% vs 30%)
3. Optionally expand parameter range for the weaker direction

### Example

```
Mutation affected spaces: sell
Locked spaces: buy (min_single_strength=0.597, min_cont_strength=0.853)
               roi (0: 0.20)
               stoploss (-0.15)
Optimizing spaces: sell (atr_sl_mult, tp1_atr_mult, trail_atr_mult)
Param count: 3
Dynamic epochs: max(200, 3 * 1.0 * 50) = 200
Long/Short: long losing -> 70% epochs for long exit params
```

### New Module: `epochs_calculator.py`

Functions:
- `calculate_epochs(param_count, range_factor, config)` -> int
- `analyze_affected_spaces(original_code, mutated_code)` -> list[str]
- `lock_parameters(strategy_params, spaces_to_lock)` -> dict
- `allocate_long_short_budget(is_metrics)` -> dict

---

## Part 3: Blacklist Lesson System

### Current Problem

LLM repeats same mistakes (40+ iterations adding trend filters causing OOS collapse).

### Storage Structure

Per-strategy isolation, one JSON file per strategy:

```
user_data/autoresearch/blacklists/
├── macd_v4.json        # strategy-specific blacklist
├── ENEW.json           # strategy-specific blacklist
└── global.json         # cross-strategy lessons (user-confirmed only)
```

### Entry Format

```json
{
  "rules": [
    {
      "id": "BL001",
      "pattern": "adding_trend_filter",
      "description": "禁止添加 EMA/SMA 趋势过滤器作为入场门控",
      "reason": "连续 15 次实验验证：趋势过滤器导致 OOS 交易数从 146 降至 <10",
      "created_at": "2026-05-31",
      "failed_count": 15,
      "source_experiments": ["E0001", "E0002"]
    }
  ]
}
```

### Workflow

1. **Auto-generation**: After 3 consecutive similar failures, system extracts pattern and creates blacklist entry
2. **Manual management**: User can add/remove/adjust blacklist entries
3. **Prompt injection**: Blacklist injected as hard constraints at top of mutation prompt
4. **Cross-strategy sharing**:
   - Rule appears in 3+ strategy blacklists -> candidate for `global.json`
   - Promotion to `global.json` requires **user confirmation**
   - `global.json` rules apply to all strategies

### Prompt Injection Position

```
[黑名单 - 必须遵守]
- BL001: 禁止添加趋势过滤器作为入场门控
- BL002: 禁止使用成交量倍数作为入场条件
[以上规则来自 15+ 次失败实验验证，违反将直接被拒绝]

[当前策略代码]
...
```

### New Module: `blacklist.py`

Functions:
- `load_blacklist(strategy_name)` -> list[Rule]
- `add_rule(strategy_name, rule)` -> None
- `remove_rule(strategy_name, rule_id)` -> None
- `detect_failure_pattern(recent_experiments)` -> Rule | None
- `inject_into_prompt(prompt, rules)` -> str
- `check_global_promotion(strategy_name, rule)` -> bool
- `propose_global_rule(rule)` -> None (flags for user confirmation)

---

## Part 4: Pre-Evolution Check + Baseline Health Assessment

### Current Problem

Strategies with losing baselines waste evolution time (macd_v4 IS -11.58%, OOS -18.77%).

### Design: Pre-Evolution Checklist

On startup, display comprehensive info and wait for user confirmation:

```
=== AutoResearch 进化前检查 ===

[文件路径]
  策略文件: user_data/strategies/macd_v4.py
  参数文件: user_data/strategies/macd_v4.json
  配置文件: user_data/config_macd_v4_live.json
  进化配置: user_data/autoresearch/evolution_config_macd_v4.json
  数据库:   user_data/dbs/macd_v4.sqlite
  结果目录: user_data/autoresearch/results/

[策略信息]
  策略名: macd_v4
  时间周期: 15m
  交易对: DOGE/USDT:USDT, LINK/USDT:USDT, TAO/USDT:USDT
  交易模式: futures (isolated)
  杠杆: 2x
  最大持仓: 5
  Hyperopt 参数: 5 个
    buy:  min_single_strength [0.20, 0.60] = 0.597
          min_cont_strength   [0.40, 0.90] = 0.853
    sell: atr_sl_mult    [1.0, 2.5] = 2.163
          tp1_atr_mult   [1.5, 3.5] = 1.597
          trail_atr_mult [1.5, 3.5] = 1.591

[基线回测 - 训练集 2024-07-01 ~ 2026-02-01 (580 天)]
  总收益:     -115.80 USDT (-11.58%)     ⚠️ 亏损
  Sharpe:     -0.36                       ⚠️ 负值
  Sortino:    -0.72
  Calmar:     -1.11
  Profit Factor: 0.96
  总交易:     740 笔
  胜率:       59.2% (438胜 / 302负)
  多头:       359 笔, -176.07 USDT (-17.61%)  ⚠️ 严重亏损
  空头:       381 笔, +60.27 USDT (+6.03%)     ✓ 盈利
  最大回撤:   34.42% (389.98 USDT)
  回撤持续:   209 天
  最佳交易对: TAO/USDT:USDT +4.39%
  最差交易对: LINK/USDT:USDT -17.18%
  平均持仓:   12h40m

[基线回测 - 测试集 2026-02-01 ~ 2026-05-26 (114 天)]
  总收益:     -187.74 USDT (-18.77%)     ⚠️ 亏损
  Sharpe:     -4.53                       ⚠️ 极差
  Sortino:    -8.12
  Calmar:     -16.46
  Profit Factor: 0.60
  总交易:     146 笔
  胜率:       53.4% (78胜 / 68负)
  多头:       82 笔, -122.95 USDT (-12.30%)  ⚠️ 亏损
  空头:       64 笔, -64.79 USDT (-6.48%)    ⚠️ 亏损
  最大回撤:   19.11% (191.93 USDT)
  回撤持续:   112 天
  最佳交易对: DOGE/USDT:USDT -3.00%        ⚠️ 全亏损
  最差交易对: TAO/USDT:USDT -12.47%
  平均持仓:   14h19m
  市场涨幅:   +10.41%

[健康评估]
  ⚠️ 基线策略在训练集和测试集均亏损
  ⚠️ 测试集表现远差于训练集 (Sharpe -4.53 vs -0.36)
  ⚠️ 做多亏损严重，建议优化或禁用做多方向
  ⚠️ 测试集所有交易对均为负收益
  ⚠️ 测试集市场上涨 10.41% 但策略亏损 18.77%

[进化配置]
  进化模式:     parameter_first (参数优先)
  最大迭代:     50
  最大运行时间: 24 小时
  参数收敛轮数: 3
  参数改善阈值: 5%
  停滞上限:     10 轮无改善触发重启

  LLM 配置:
    默认 provider: kimi
    备用顺序:     kimi -> glm -> deepseek
    Temperature:  0.7
    Max Tokens:   32768

  Hyperopt 配置:
    Epochs:       200~500 (动态计算)
    优化空间:     buy, sell, roi, stoploss
    并行数:       8
    早停:         100 epochs
    损失函数:     AutoResearchMultiMetricLoss

  安全限制:
    最大回撤:     30%
    最少 IS 交易: 30
    最少 OOS 交易: 10
    OOS 最大回撤: 25%
    要求 OOS 盈利: 是

  评分权重:
    利润: 30%, Sharpe: 25%, 盈亏比: 15%
    回撤: 15%, 交易数: 10%, 胜率: 5%
    IS 权重: 25%, OOS 权重: 75%

  黑名单规则:   2 条
    BL001: 禁止添加趋势过滤器作为入场门控
    BL002: 禁止使用成交量倍数作为入场条件

确认开始进化？[y/N]
```

### Health Assessment Rules

- IS + OOS both losing -> strong warning (still allows user to proceed)
- Sharpe < -1.0 -> suggest strategy adjustment
- Long/short difference > 20% -> suggest optimizing weak direction
- Trade count < 30 -> warn insufficient statistical significance

### db-url Integration

- Auto-detect/create `user_data/dbs/` directory
- New `db_path` field in evolution config (default: `user_data/dbs/`)
- Backtesting automatically uses `--db-url sqlite:///user_data/dbs/<strategy>.sqlite`

### New Module: `precheck.py`

Functions:
- `run_precheck(config)` -> bool (user confirmed)
- `run_baseline_backtest(config, timerange)` -> dict (full metrics)
- `assess_health(is_metrics, oos_metrics)` -> list[str] (warnings)
- `format_precheck_report(config, is_metrics, oos_metrics, warnings, blacklist_rules)` -> str
- `ensure_db_directory(db_path)` -> None

---

## Part 5: Smart Restart Mechanism

### Current Problem

40+ consecutive iterations without improvement, LLM trapped in local optimum.

### Stagnation Detection

- Track consecutive iterations without improvement
- When reaching `stagnation_limit` (default 10), trigger restart

### Restart Procedure (Sequential)

```
Step 1: Analyze stagnation cause
  ├── Review all failed experiments, extract common failure patterns
  ├── Generate new blacklist rules
  └── Record to strategy-specific blacklist

Step 2: Adjust evolution direction
  ├── If long/short gap large -> next mutation focus on weak direction only
  ├── If OOS trades too few -> next mutation focus on entry logic (expand signals)
  ├── If OOS losses severe -> next mutation focus on exit/stoploss logic
  └── Select most urgent direction as next round's "focus"

Step 3: Relax safety thresholds (optional, temporary)
  ├── min_oos_trade_count: 10 -> 5
  ├── require_oos_positive: true -> false
  └── Relaxation lasts 3 rounds only, then restore original thresholds

Step 4: Record restart history
  └── Write to tracker, avoid repeating same restart strategy
```

### Infinite Restart Prevention

- Maximum 3 restarts per evolution run
- After 3 restarts with no improvement -> stop evolution, output full report for user decision

### Cross-Strategy Knowledge Sharing

- Rule appears in 3+ strategy blacklists -> candidate for `global.json`
- Promotion to `global.json` requires **user confirmation** (not automatic)
- `global.json` rules apply to all strategies

---

## Part 6: Data Flow Summary

```
evolve.py startup
  -> precheck.py (check + user confirmation)
  -> [Loop start]
    -> epochs_calculator.py (calculate epochs + lock spaces)
    -> hyperopt_runner.py (parameter optimization phase)
    -> Check convergence?
      -> Not converged -> continue parameter optimization
      -> Converged -> blacklist.py (inject lessons) + mutator.py (LLM mutation)
    -> safety.py + evaluator.py (validation)
    -> Stagnation detection?
      -> Trigger restart -> blacklist.py (generate new rules)
  -> [Loop end]
```

### db-url Integration

- `config.py` new `db_path` field (default `user_data/dbs/`)
- `prepare.py` injects `--db-url` when building freqtrade config

---

## Implementation Priority

1. **precheck.py** + db-url integration (highest value, prevents wasted runs)
2. **epochs_calculator.py** + incremental locking (saves significant time)
3. **blacklist.py** + prompt injection (prevents repeated mistakes)
4. **evolve.py** three-phase loop refactor (enables parameter-first mode)
5. **Smart restart** in evolve.py + tracker.py (handles stagnation)
6. **Cross-strategy sharing** in blacklist.py (long-term knowledge accumulation)

## Configuration Summary (New Fields)

```json
{
  "evolution_mode": "parameter_first",
  "param_convergence_rounds": 3,
  "param_improvement_threshold": 0.05,
  "stagnation_limit": 10,
  "max_restarts": 3,
  "db_path": "user_data/dbs/",
  "blacklist_dir": "user_data/autoresearch/blacklists/",
  "epochs_floor": 200,
  "epochs_ceiling": 1000,
  "global_blacklist_threshold": 3
}
```
