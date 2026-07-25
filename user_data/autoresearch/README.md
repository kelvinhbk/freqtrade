# AutoResearch - 策略自我进化系统

受 Karpathy autoresearch 启发，基于 LLM + freqtrade Hyperopt 的分层策略进化系统。

**核心设计**：LLM 负责策略**架构**（指标、风控框架），Hyperopt 负责**参数**优化。两者分层协作，循环进化。

---

## 目录结构

```
user_data/autoresearch/
├── config.py                 # 进化配置
├── evolve.py                 # 主进化循环（入口）
├── evaluator.py              # 回测评分
├── evolution_config_efuture.json      # Efuture 专用正式配置
├── evolution_config_enew.json         # ENEW 专用正式配置
├── evolution_config_enew_smoke.json   # ENEW 链路验证配置
├── hyperopt_runner.py        # 程序化 Hyperopt
├── mutator.py                # LLM 结构变异引擎
├── prepare.py                # 基线建立
├── safety.py                 # 四层安全门控
├── tracker.py                # 实验追踪（JSONL）
├── program.md                # LLM 系统指令
├── .env                      # API Key（gitignore）
├── .env.example              # API Key 模板
├── logs/                     # 长任务日志（gitignore）
├── results/
│   └── experiments.jsonl     # 实验日志
└── README.md                 # 本文档

user_data/hyperopts/
└── autoresearch_multi_metric.py   # 自定义 MultiMetric Loss

user_data/strategies/autoresearch_enew/
└── ENEW_E0001.py, ENEW_E0001.json, ... # ENEW 候选策略输出（gitignore）
```

---

## 快速开始

### 1. 配置 API Key

```bash
cp user_data/autoresearch/.env.example user_data/autoresearch/.env
# 编辑 .env，填入你的 API Key
```

### 2. 准备策略和配置

确保你有：
- 一个 freqtrade 策略文件，如 `user_data/strategies/MyStrategy.py`
- 一个 freqtrade 配置文件，如 `user_data/config.json`
- 策略对应的 OHLCV 数据已下载

### 3. 运行进化

```bash
cd /Users/kelvin/projects/freqtrade
.venv/bin/python user_data/autoresearch/evolve.py \
  --strategy user_data/strategies/MyStrategy.py \
  --freqtrade-config user_data/config.json \
  --evolution-config user_data/autoresearch/evolution_config_efuture.json \
  --is-range 20240801-20260201 \
  --oos-range 20260201-20260501 \
  --max-iter 10
```

### 4. 查看结果

```bash
# 查看实验日志
cat user_data/autoresearch/results/experiments.jsonl

# 查看候选策略
ls user_data/strategies/autoresearch_enew/
```

---

## ENEW 进化 Runbook

ENEW 使用独立配置和输出目录，避免候选策略污染主策略目录。

### 正式配置

| 项目 | 值 |
|------|----|
| 策略 | `user_data/strategies/ENEW.py` |
| Freqtrade 配置 | `user_data/config_enew_backtest.json`（**当前缺失**，运行 ENEW 线前必须自备 base config，路径可在 evolution_config 的 `base_config_path` 中调整） |
| 进化配置 | `user_data/autoresearch/evolution_config_enew.json` |
| 输出目录 | `user_data/strategies/autoresearch_enew/` |
| 结果文件 | `user_data/autoresearch/results/enew_live_score_v2_experiments.jsonl` |
| 时间周期 | `5m` |
| IS | `20240701-20260201` |
| OOS | `20260201-20260501` |
| Pairs | `DOT, ENJ, RENDER, SOL, TAO, TON, WLD, ZEC` USDT 永续 |
| Hyperopt | `500` epochs, `8` workers, `AutoResearchMultiMetricLoss` |
| 最大迭代 | `50` |

### 先跑 Smoke 验证

Smoke 配置只跑 1 次迭代、30 epochs，用来验证 LLM、策略写入、Hyperopt、IS/OOS、walk-forward、JSONL 记录是否完整跑通。

注意：下列命令中的 `--freqtrade-config user_data/config_enew_backtest.json` 所指文件当前缺失，运行前必须自备 base config。

```bash
cd /Users/kelvin/projects/freqtrade
.venv/bin/python user_data/autoresearch/evolve.py \
  --strategy user_data/strategies/ENEW.py \
  --freqtrade-config user_data/config_enew_backtest.json \
  --evolution-config user_data/autoresearch/evolution_config_enew_smoke.json \
  --max-iter 1
```

当前 smoke 验证结果：

| 策略 | IS Sharpe | IS PF | IS Trades | IS PnL | OOS Sharpe | OOS PnL | 结论 |
|------|-----------|-------|-----------|--------|------------|---------|------|
| `ENEW` baseline | `5.021` | `1.331` | `1413` | `920.98 USDT` | `3.148` | `77.72 USDT` | 当前最佳 |
| `ENEW_E0001` smoke | `1.418` | `2.431` | `127` | `114.88 USDT` | `0.503` | `7.72 USDT` | regression，已丢弃 |

Smoke 说明：候选策略质量不重要，重点是确认链路已跑通。正式进化仍使用 500 epochs。

### 启动正式 ENEW 进化

推荐用 `tmux` 保活长任务，并同时写入日志（注意：命令中的 `user_data/config_enew_backtest.json` 当前缺失，运行前必须自备 base config）：

```bash
cd /Users/kelvin/projects/freqtrade
mkdir -p user_data/autoresearch/logs
tmux -S /private/tmp/codex-autoresearch.sock new-session -d \
  -s enew_ar_20260521_1841 \
  -c /Users/kelvin/projects/freqtrade \
  '.venv/bin/python user_data/autoresearch/evolve.py --strategy user_data/strategies/ENEW.py --freqtrade-config user_data/config_enew_backtest.json --evolution-config user_data/autoresearch/evolution_config_enew.json 2>&1 | tee -a user_data/autoresearch/logs/enew_evolution_20260521_1841.log'
```

### 监控正式任务

```bash
# 看实时日志
tail -f user_data/autoresearch/logs/enew_evolution_20260521_1841.log

# 看最近实验记录
tail -n 20 user_data/autoresearch/results/enew_live_score_v2_experiments.jsonl

# 统计状态
cat user_data/autoresearch/results/enew_live_score_v2_experiments.jsonl | jq -r '.status' | sort | uniq -c

# 查看 tmux session
tmux -S /private/tmp/codex-autoresearch.sock list-sessions

# 进入 tmux
tmux -S /private/tmp/codex-autoresearch.sock attach -t enew_ar_20260521_1841
```

### 当前正式任务状态

旧正式任务已跑出 `ENEW_E0023` 这类低回撤候选，但从实盘收益/Sharpe 看不如 baseline。当前正式配置已切到 stricter live gate，并使用新的结果文件：

```text
user_data/autoresearch/results/enew_live_score_v2_experiments.jsonl
```

旧结果保留在：

```text
user_data/autoresearch/results/enew_experiments.jsonl
```

期间 Kimi/GLM/DeepSeek 可能出现 `503 Service Unavailable`，系统会自动按 `kimi -> glm -> deepseek` fallback 并继续下一轮。

### 实盘前筛选标准

不要只看 IS 最优结果。建议至少满足：

- OOS PnL 高于 baseline `77.72 USDT`
- OOS Sharpe 高于 baseline `3.148`，或 Sharpe 接近但回撤明显更低
- OOS PF 不低于 baseline `1.349`
- OOS 交易数不少于 `30`，避免样本太小
- Walk-forward 至少 `2/3` 分段盈利
- 单一 pair 贡献不过度集中，尤其避免只靠 ENJ 一组行情撑住
- 回撤、止损退出、trailing_stop_loss 占比要比 baseline 更健康

当前 live gate 已配置：

| Gate | 阈值 |
|------|------|
| OOS PnL vs baseline | `>= 1.00x` |
| OOS Sharpe vs baseline | `>= 0.85x` |
| OOS PF vs baseline | `>= 1.00x` |
| OOS DD vs baseline | `<= 1.20x` |
| OOS trades vs baseline | `>= 0.40x` |
| IS PnL vs baseline | `>= 0.60x` |
| IS Sharpe vs baseline | `>= 0.60x` |

---

## 配置文件

可通过 `--evolution-config` 传入 JSON 文件，覆盖默认配置。

### 示例 `evolution_config.json`

```json
{
  "llm": {
    "default_provider": "kimi",
    "fallback_order": ["kimi", "glm", "deepseek"],
    "temperature": 0.7,
    "max_tokens": 32768,
    "request_timeout": 120,
    "max_retries": 3,
    "providers": {
      "kimi": {
        "base_url": "https://api.kimi.com/coding",
        "api_key_env": "KIMI_API_KEY",
        "model": "kimi-2.6"
      },
      "glm": {
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "api_key_env": "GLM_API_KEY",
        "model": "glm-5.1"
      },
      "deepseek": {
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-pro"
      }
    }
  },
  "hyperopt": {
    "loss_function": "SharpeHyperOptLoss",
    "epochs": 500,
    "spaces": ["buy", "sell", "roi", "stoploss"],
    "jobs": -1,
    "random_state": 42,
    "min_trades": 30,
    "early_stop": 0
  },
  "safety": {
    "max_drawdown_pct": 0.15,
    "min_trade_count": 30,
    "min_oos_trade_count": 10,
    "max_oos_drawdown_pct": 0.25,
    "require_oos_positive": true,
    "max_sharpe_degradation": 5.0
  },
  "score_weights": {
    "profit": 0.30,
    "sharpe": 0.25,
    "profit_factor": 0.15,
    "drawdown": 0.15,
    "trade_count": 0.10,
    "win_rate": 0.05,
    "calmar": 0.0,
    "in_sample": 0.25,
    "out_of_sample": 0.75
  },
  "max_iterations": 50,
  "max_walltime_hours": 24,
  "strategy_path": "user_data/strategies/ENEW.py",
  "base_config_path": "user_data/config_enew_backtest.json",
  "strategy_output_dir": "user_data/strategies/autoresearch_enew",
  "results_file": "user_data/autoresearch/results/enew_live_score_v2_experiments.jsonl",
  "timeframe": "5m",
  "in_sample_timerange": "20240801-20260201",
  "out_of_sample_timerange": "20260201-20260501"
}
```

注意：示例中的 `base_config_path`（`user_data/config_enew_backtest.json`）所指文件当前缺失，运行 ENEW 线前必须自备 base config。

### 配置字段说明

#### LLM 配置 (`llm`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `default_provider` | string | 默认 provider，当前为 `kimi` |
| `fallback_order` | list | provider fallback 顺序，当前为 `kimi -> glm -> deepseek` |
| `temperature` | float | 采样温度 (0-1) |
| `max_tokens` | int | 最大输出 token |
| `request_timeout` | int | 单次 LLM 请求超时时间（秒） |
| `max_retries` | int | 每个 provider 的重试次数 |
| `providers.*.base_url` | string | Anthropic 兼容 API 地址 |
| `providers.*.api_key_env` | string | `.env` 中的环境变量名 |
| `providers.*.model` | string | 模型名称 |

当前默认配置：

| Provider | 用途 | Base URL | Model | API Key env |
|----------|------|----------|-------|-------------|
| `kimi` | Kimi Coding Plan | `https://api.kimi.com/coding` | `kimi-2.6` | `KIMI_API_KEY` |
| `glm` | GLM Coding Plan | `https://open.bigmodel.cn/api/anthropic` | `glm-5.1` | `GLM_API_KEY` |
| `deepseek` | DeepSeek API | `https://api.deepseek.com/anthropic` | `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |

所有 provider 当前都走 Anthropic-compatible `/v1/messages` 协议，代码只维护一套调用逻辑。`.env` 只保存在本地，禁止提交。

#### Hyperopt 配置 (`hyperopt`)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `loss_function` | `SharpeHyperOptLoss` | Loss 函数名称。可选内置：`SharpeHyperOptLoss`、`CalmarHyperOptLoss`、`SortinoHyperOptLoss`、`ProfitDrawDownHyperOptLoss` 等。也可选自定义：`AutoResearchMultiMetricLoss` |
| `epochs` | 500 | 每次结构变异的参数优化 epoch 数 |
| `spaces` | `["buy","sell","roi","stoploss"]` | 优化空间。可选：`buy`、`sell`、`roi`、`stoploss`、`trailing`、`protection`、`all` |
| `jobs` | -1 | 并行 workers，-1 表示使用全部 CPU |
| `random_state` | null | 随机种子，保证可复现 |
| `min_trades` | 30 | Hyperopt 最小交易数过滤 |
| `early_stop` | 0 | 早停 epoch 数，0 表示不早停 |
| `custom_loss_weights` | object | 仅用于 `AutoResearchMultiMetricLoss`，覆盖默认权重 |
| `custom_loss_targets` | object | 仅用于 `AutoResearchMultiMetricLoss`，覆盖归一化目标值 |

#### 安全限制 (`safety`)

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_drawdown_pct` | 0.20 | 样本内最大允许回撤 |
| `min_trade_count` | 30 | 样本内最小交易数 |
| `min_oos_trade_count` | 10 | 样本外最小交易数 |
| `max_oos_drawdown_pct` | 0.25 | 样本外最大允许回撤 |
| `require_oos_positive` | true | 样本外必须盈利 |
| `max_sharpe_degradation` | 5.0 | IS/OOS Sharpe 退化倍数上限 |

#### 评分权重 (`score_weights`)

综合评分采用 baseline-relative live score：baseline 约等于 `1.0`，候选超过 `1.0` 才代表综合排序优于 baseline。默认先做安全门槛，再按 OOS 优先排序：
- `out_of_sample`: 0.75
- `in_sample`: 0.25
- `profit`: 0.30
- `sharpe`: 0.25
- `profit_factor`: 0.15
- `drawdown`: 0.15
- `trade_count`: 0.10
- `win_rate`: 0.05
- `calmar`: 0.00

`trade_count` 只作为样本量评分，超过 baseline 不额外加分；`drawdown` 使用 baseline 回撤 / 候选回撤，回撤越低分越高；`calmar` 默认不参与主评分，避免和 PnL/DD 重复计权。

---

## 自定义 Loss Function

系统内置了一个自定义 Loss：`AutoResearchMultiMetricLoss`。

### 使用方式

在 `evolution_config.json` 中设置：

```json
{
  "hyperopt": {
    "loss_function": "AutoResearchMultiMetricLoss",
    "custom_loss_weights": {
      "sharpe": 0.35,
      "profit_factor": 0.20,
      "calmar": 0.20,
      "win_rate": 0.10,
      "trade_density": 0.15
    },
    "custom_loss_targets": {
      "sharpe": 2.5,
      "profit_factor": 1.5,
      "calmar": 2.0,
      "win_rate": 0.55,
      "trade_density": 1.0
    }
  }
}
```

### 组合策略（多 Loss 同时优化）

如需对同个结构变体跑多个 Loss 取最好结果，目前需要手动修改 `hyperopt_runner.py` 中的逻辑。未来版本会支持配置化。

---

## 进化流程详解

```
初始化
  1. 加载配置
  2. 建立基线（如有现成 .json 参数则直接用，否则先跑 hyperopt）

进化循环 (iter 1..N)
  Phase 1 - LLM 提出结构变异
    分析当前最佳策略 + 最近实验历史
    生成结构变更（指标、风控、逻辑框架）

  Phase 2 - 静态检查 (L1)
    AST 验证类结构完整
    危险代码检测

  Phase 3 - 写入策略文件
    将变异代码写入 strategy_output_dir/{BaseName}_E{N}.py
    继承父策略的 .json 参数（不优化空间保留原值）

  Phase 4 - 智能空间检测
    AST 对比 entry/exit/stoploss 方法
    未变化的空间从 hyperopt 中剔除，节省优化时间

  Phase 5 - Hyperopt 参数优化 (IS)
    在样本内时间范围运行 freqtrade hyperopt
    自动导出最优参数到 .json

  Phase 6 - IS 回测（带最优参数）
    加载 hyperopt 后的参数运行回测

  Phase 7 - 硬限制检查 (L2)
    交易数 >= 30, 回撤 <= 15%, 盈利 > 0

  Phase 8 - OOS 回测（相同参数）
    在样本外时间范围运行回测

  Phase 9 - OOS 验证 (L3)
    OOS 交易数 >= 10, 盈利, 过拟合检测

  Phase 9b - Baseline-relative live gate
    OOS PnL/PF/Sharpe/DD/交易数必须达到 baseline 相对门槛

  Phase 10 - 综合评分与决策
    baseline-relative live score，baseline 约等于 1.0
    score > best_score -> 保留改进
    score <= best_score -> 丢弃退化
```

---

## CLI 参数

```
python evolve.py [选项]

必选:
  --strategy STRATEGY          基础策略 .py 文件路径
  --freqtrade-config CONFIG    freqtrade 配置 .json 路径

可选:
  --evolution-config CONFIG    进化配置 .json 路径（默认使用内置默认值）
  --is-range RANGE             样本内时间范围，如 20240801-20260201
  --oos-range RANGE            样本外时间范围，如 20260201-20260501
  --max-iter N                 最大迭代次数
  --max-hours H                最大运行时间（小时）
```

---

## 最佳实践

### 1. 先从少量迭代测试

首次使用建议先跑 smoke 配置，确认流程通顺后再跑正式配置。

### 2. Epoch 数根据参数空间调整

| 优化空间数 | 建议 Epoch | 预估时间（8核） |
|-----------|-----------|---------------|
| 1-2 (如 buy+sell) | 200-300 | 15-25 min |
| 3-4 (默认) | 500 | 40-60 min |
| 5+ (含 trailing/protection) | 800-1000 | 1.5-2.5 h |

### 3. 使用已优化策略作为基线

如果策略已有 hyperopt 参数（`.json` 文件），基线会直接使用，跳过基线 hyperopt，大幅节省时间。

### 4. 监控实验日志

```bash
# 实时查看最新实验
tail -f user_data/autoresearch/results/experiments.jsonl | jq .

# 统计改进/退化次数
cat user_data/autoresearch/results/experiments.jsonl | jq -r '.status' | sort | uniq -c
```

### 5. 清理临时策略

被拒绝的策略文件会自动删除。如需手动清理：

```bash
rm user_data/strategies/autoresearch_enew/ENEW_E*.py
rm user_data/strategies/autoresearch_enew/ENEW_E*.json
```

---

## 故障排查

### Hyperopt lock timeout

如果看到 `Another hyperopt instance is running`，说明上次 hyperopt 异常退出未释放锁。

```bash
rm user_data/hyperopt.lock
```

### No data found

确保已下载对应时间范围的 OHLCV 数据：

```bash
freqtrade download-data --config user_data/config.json \
  --timerange 20240801-20260501 --timeframe 5m
```

### LLM 未返回有效代码

检查 API Key 是否正确，以及模型是否支持长输出（代码可能超过 max_tokens）。当前正式配置使用 `max_tokens=32768`。如果日志里出现 `503 Service Unavailable`，通常是 provider 临时不可用，系统会自动重试和 fallback。

### Candidate strategy cannot be loaded

如果出现 `Impossible to load Strategy 'ENEW_E0001'`，优先确认 `strategy_path` 是否指向候选策略目录。程序化 hyperopt/backtest 必须携带：

```text
strategy_path = user_data/strategies/autoresearch_enew
```

当前版本已在 `hyperopt_runner.py` 和 `prepare.py` 中传入 `strategy_path`。

### OOS 表现持续退化

可能原因：
1. 时间范围选择不当（IS/OOS 市场结构差异过大）
2. 基线策略本身在 OOS 已失效
3. 需要更长的 IS 数据或更保守的安全限制

---

## 架构说明

### 为什么分层设计？

| 能力 | LLM | Hyperopt |
|------|-----|----------|
| 参数搜索 | 弱（随机猜测） | 强（贝叶斯优化） |
| 结构创新 | 强（理解指标逻辑） | 无（只能调已有参数） |
| 过拟合控制 | 弱 | 强（搜索空间约束） |
| 速度 | 慢（API 调用） | 快（本地计算） |

分层协作让各自做擅长的事，整体效率远高于纯 LLM 或纯 Hyperopt。

### 智能空间检测

系统通过 AST 对比 `populate_entry_trend` 等方法，自动判断 LLM 是否修改了对应逻辑：

- 没改 entry -> 不优化 `buy` 空间（省 20-30% 时间）
- 没改 exit -> 不优化 `sell` 空间
- 没改 custom_stoploss -> 不优化 `stoploss` 空间

对于未优化的空间，新策略自动继承父策略的最优参数，保证性能不下降。
