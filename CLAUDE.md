# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Freqtrade is a crypto trading bot written in Python (>=3.11). It supports spot and futures trading across multiple exchanges (Binance, Bybit, OKX, Gate.io, Kraken, etc.), with backtesting, hyperparameter optimization (hyperopt), machine learning (FreqAI), and management via Telegram/WebUI/REST API.

## 我的电脑配置

- 电脑型号：MacBook Pro (14-inch, M3 Max, 2023)
- 操作系统：macOS Sonoma 14.4.1
- 内存：128GB
- 处理器：Apple M3 Max
- 存储空间：2TB
- 在参数优化的时候至少使用8个核心

## Build & Development Commands

### Setup
```bash
pip install -e ".[dev]"        # Full dev install (all extras + develop deps)
pip install -e ".[develop]"    # Core + lint/test tools only
pre-commit install             # Install git pre-commit hooks
```

### Testing
```bash
pytest                                              # Run all tests
pytest tests/test_configuration.py                  # Single test file
pytest tests/test_configuration.py::test_method     # Single test case
pytest --random-order --cov=freqtrade --cov-config=.coveragerc tests/  # Full CI run with coverage
pytest -n auto                                      # Parallel execution (xdist, loadscope scheduling)
pytest --longrun                                    # Include long-running tests
```

Test configuration lives in `pyproject.toml [tool.pytest]`. Async tests use `asyncio_mode = "auto"`. The `conftest.py` provides shared fixtures for exchanges, trades, and bot instances. Key mock constants: `EXMS = "freqtrade.exchange.exchange.Exchange"`, `CURRENT_TEST_STRATEGY = "StrategyTestV3"`.

### Linting & Type Checking
```bash
pre-commit run -a      # Run ALL checks (ruff, mypy, codespell, schema validation)
ruff check .           # Lint only
ruff format .          # Format only
mypy freqtrade         # Type check
```

Ruff config: line-length=100, max-complexity=12. Mypy uses `ignore_missing_imports = true` with SQLAlchemy plugin.

### Running the Bot
```bash
freqtrade trade                          # Live/dry-run trading
freqtrade backtesting                    # Backtest a strategy
freqtrade hyperopt                       # Hyperparameter optimization
freqtrade download-data                  # Download historical data
freqtrade webserver                      # Start API server
```

Entry point: `freqtrade.main:main` (defined in `pyproject.toml [project.scripts]`).

## Architecture

### Core Loop
`main.py` -> `Worker` -> `FreqtradeBot` (the central orchestrator). The `Worker` runs the main loop, calling `FreqtradeBot.process()` each iteration. `FreqtradeBot` coordinates exchange interaction, strategy execution, trade management, and RPC notifications.

### Strategy System
- `IStrategy` (`strategy/interface.py`) is the abstract base class all user strategies extend. It inherits from `HyperStrategyMixin` for hyperopt parameter support.
- Key methods to override: `populate_indicators()`, `populate_entry_trend()`, `populate_exit_trend()`
- The `@informative` decorator (`strategy/informative_decorator.py`) injects higher-timeframe data
- Strategies are loaded dynamically from `user_data/strategies/` via `StrategyResolver` (`resolvers/strategy_resolver.py`), which uses `IResolver` to import Python files at runtime

### Exchange Layer
- `Exchange` (`exchange/exchange.py`) wraps ccxt with unified interface
- Exchange-specific overrides live in separate files (e.g., `binance.py`, `bybit.py`, `okx.py`)
- WebSocket support: `exchange_ws.py`

### Data Pipeline
- `DataProvider` (`data/dataprovider.py`) is the common interface for both live and backtest data access, used by strategies via `self.dp`
- `data/history/` handles OHLCV data storage/loading (feather, json, parquet formats)
- `data/converter/` handles format conversions (ohlcv, trades, orderbook)

### Persistence
- SQLAlchemy ORM with SQLite backend (`persistence/`)
- Core models: `Trade`, `Order`, `PairLocks` in `trade_model.py`
- Database migrations in `persistence/migrations.py`

### RPC & API
- `RPCManager` dispatches events to registered handlers (Telegram, Discord, Webhook, API server)
- `rpc/api_server/` contains a FastAPI-based REST/WebSocket API with JWT auth
- `rpc/telegram.py` handles Telegram bot commands

### Plugins
- **Pairlists** (`plugins/pairlist/`): Chained filters that determine which pairs to trade (StaticPairList, VolumePairList, etc.)
- **Protections** (`plugins/protections/`): Guard rails (CooldownPeriod, MaxDrawdown, StoplossGuard)

### Hyperopt
- `optimize/hyperopt/` uses Optuna for parameter optimization
- Loss functions in `optimize/hyperopt_loss/` define optimization objectives (Sharpe, Calmar, max drawdown, etc.)
- `optimize/backtesting.py` runs historical simulations

### FreqAI (Machine Learning)
- `freqai/freqai_interface.py` is the base for ML model integrations
- Models: LightGBM, XGBoost, etc. (`freqai/prediction_models/`)
- Reinforcement learning: `freqai/RL/`, `freqai/torch/`

### Resolvers (Dependency Injection)
All user-customizable components are loaded via resolvers inheriting from `IResolver` (`resolvers/iresolver.py`): `StrategyResolver`, `ExchangeResolver`, `HyperoptResolver`, `FreqaiModelResolver`, `PairlistResolver`, `ProtectionResolver`. They dynamically import Python files from user_data directories.

### Configuration
- JSON config files validated against a JSON schema (`config_schema/config_schema.py`)
- `configuration/configuration.py` handles loading, merging CLI args + config file + env vars
- Key config concepts: `dry_run` mode, `trading_mode` (spot/futures), `stake_currency`

## Code Style Conventions

- Docstrings: reST format (`:param xxx:`, `:return:`, `:raises KeyError:`), double-quoted
- All public methods must have docstrings
- PRs target `develop` branch (not `stable`)
- New features require unit tests and documentation
- Pre-commit hooks are mandatory: `pre-commit install`

## Key Test Patterns

- `conftest.py` provides `default_conf` fixture (mock config dict) and exchange mocking via `EXMS` patch target
- Trade fixtures in `conftest_trades.py` and `conftest_trades_usdt.py` provide pre-built trade objects
- Exchange responses are mocked at the ccxt level via `patch(EXMS + ".<method>")`
- `time-machine` is used for time-dependent tests

## user_data 组织规范

策略按线组织：`strategies/<线名>/`（efuture-iter、efuture-long、efuture-long-kelvin、efuture-short、enew、freqai-v6），实验产物在 `strategies/experiments/`。config 按环境三分：`configs/live|dryrun|backtest/`，密钥在 `configs/secrets.local.json`（不入库，启动用多 -c 合并）。

命名：文件名只表达"是什么"——代际 `_v<N>` 表达版本，目录表达状态，实验号仅在 autoresearch 产物中存在。每线谱系见各目录 LINEAGE.md，完整规范见 user_data/README.md。

## Autoresearch 进化系统运维记录

### 常见问题与修复

#### 1. LLM API 模型名称错误导致调用卡住
- **现象**：mutator 记录 `Prompt length` 后无任何响应，进程 CPU 0%
- **根因**：配置中 `glm-5.1` 模型返回空内容，OpenAI client 的 httpx 连接池在空响应后进入损坏状态
- **修复**：`evolution_config_efuture.json` 中模型改为 `glm-4-plus`
- **验证**：`.venv/bin/python -c "..."` 直接测试 API 响应

#### 2. joblib semlock 泄漏导致新进程挂起
- **现象**：`kill -9` 终止旧进程后，新进程启动但 LLM 调用永远不返回
- **根因**：loky/multiprocessing 的 semlock 未清理，macOS 信号量上限（~128-256）被耗尽
- **修复**：
  ```bash
  ipcs -s | tail -n +2 | awk '{print $2}' | while read id; do ipcrm -s "$id"; done
  ```
- **检查**：`ipcs -s | grep -c "^s"`

#### 3. httpx 连接池在 backtest 后损坏
- **现象**：前几次 LLM 调用正常，之后连续返回 "Connection error" 或 600 秒超时
- **根因**：OpenAI client 复用 httpx.Client，freqtrade backtest 内的 multiprocessing 影响连接池状态
- **修复**（`mutator.py`）：每次 `_call_llm()` 内新建 `httpx.Client(timeout=600)`，传入 `OpenAI(http_client=...)`，并在 `finally` 中 `http_client.close()`
- **额外修复**：单次超时从 600 秒缩短到 120 秒，重试循环同时捕获 `TimeoutError` 和 `ConnectionError`，3 次指数退避（2s, 4s, 8s）

#### 4. 进程疯狂消耗迭代次数
- **现象**：mutation/hyperopt 失败后 1 秒内跳到下一个 iteration，几分钟内消耗 15+ 次迭代
- **修复**（`evolve.py`）：`continue` 前添加 `time.sleep(5)`，给 API 限流恢复留出时间

#### 5. LLM 生成代码的 freqtrade API 兼容性错误
- **现象**：hyperopt 阶段大量 `AttributeError: 'LocalTrade' object has no attribute 'entry_tag'`
- **根因**：freqtrade 将 `entry_tag` 重命名为 `enter_tag`，LLM 训练数据包含旧 API
- **修复**（`mutator.py`）：添加 `_fix_entry_tag()` 自动替换 `trade.entry_tag` -> `trade.enter_tag`
- **同类修复**：`_fix_is_long()`（`is_long` -> `not is_short`）、`_fix_scalar_methods()`（禁止 scalar 上调用 `.shift()`/`.rolling()`）

#### 6. 监控通知过滤
- **建议**：`grep --line-buffered` 过滤掉 `strategy_wrapper` 的逐行 ERROR，避免通知轰炸。只监控：
  ```
  Iteration [0-9]+|IMPROVEMENT|Regression|Hyperopt found|Mutation failed
  ```
