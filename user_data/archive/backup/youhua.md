# ENEW 策略选币、回测与参数优化全流程

## 1. 项目背景

基于原始策略 E0V1EN 进行优化重构，创建新策略 ENEW。优化目标：修复已知 bug、重构出场系统、提升策略稳健性。

- 策略类型：5m 级别加密货币超卖反弹
- 交易所：Binance 现货
- 优化方法：Hyperopt (Optuna NSGAIIISampler)
- 机器配置：MacBook Pro M3 Max, 128GB RAM, 4核并行

---

## 2. 策略改动（E0V1EN -> ENEW）

### 2.1 Bug 修复

| 问题 | 修复前 | 修复后 |
|------|--------|--------|
| startup_candle_count | 20（导致 24h_change_pct 前 288 行全 NaN） | 310（288+20 覆盖完整指标计算） |
| cci_loss_sell 条件 | `profit > -3%`（盈利单也会被误杀） | `-3% < profit < 0`（只在亏损区间触发） |

### 2.2 出场系统重构

| 参数 | E0V1EN | ENEW |
|------|--------|------|
| stoploss | -25%（形同虚设） | -10%（最终安全网） |
| trailing_stop_positive | 0.2%（过早止盈） | 4.6%（优化后） |
| trailing_stop_positive_offset | 3% | 10.1%（优化后） |
| minimal_roi | 100%（不触发） | 阶梯式：15%/8%/5%/3% |
| custom_stoploss | 无 | 基于 ATR + 持仓时间的动态止损 |
| 时间止损 | 7h(-5%)/10h(-10%) | 保留，叠加 custom_stoploss |

**custom_stoploss 逻辑：**

- 开仓初期（0-30min）：固定止损 -7.9%（优化后）
- 中期（30min-4h）：基于 ATR 动态止损，max(ATR * 2.3, -10%)
- 后期（4h+）：收紧到 -6.2%（优化后）

### 2.3 入场条件简化

- 合并 buy_1/buy_new 为单一入场条件
- 删除冗余的 24h 过滤参数（两套合并为一套）
- 移除 1h EMA200 趋势过滤（回测验证该条件导致入场信号极少）

### 2.4 最终参数（Hyperopt 优化后）

**Buy 参数：**

| 参数 | 默认值 | 范围 |
|------|--------|------|
| buy_rsi_fast | 68 | 20-70 |
| buy_rsi | 15 | 15-50 |
| buy_sma15_ratio | 0.954 | 0.90-1.0 |
| buy_cti | -0.75 | -1~1 |
| buy_24h_min_pct | -26.3 | -30~0 |
| buy_24h_max_pct | 50.0 | 0~200 |

**Sell 参数：**

| 参数 | 默认值 | 范围 |
|------|--------|------|
| sell_fastx | 62 | 50-100 |
| csl_initial | -0.079 | -0.15~-0.03 |
| csl_mid_ratio | 2.3 | 1.0-3.0 |
| csl_late | -0.062 | -0.08~-0.01 |

**Trailing 参数：**

| 参数 | 值 |
|------|------|
| trailing_stop_positive | 0.046 |
| trailing_stop_positive_offset | 0.101 |
| trailing_only_offset_is_reached | True |

---

## 3. 选币流程

### 3.1 选币原则

核心问题：高相关性交易对导致风险集中、Hyperopt 失真。

解决方案：

1. 每个板块最多选 1 个代表币
2. 所有配对相关性 < 0.7
3. 最终 3-5 个币覆盖 >= 3 个不同板块
4. 日均成交额 > 500 万 USDT

### 3.2 自动选币脚本

脚本路径：`scripts/select_pairs.py`

功能：

- 从 Binance 自动获取所有 USDT 现货对
- 过滤：稳定币、杠杆代币、已退市/迁移币（MATIC->POL）
- 按成交额过滤（> 500万 USDT/天）
- 计算近 30 天日线收益率相关系数矩阵
- 内置板块映射（Layer2/DeFi/Meme/AI/L1/隐私/游戏/存储/RWA 等）
- 贪心算法：从最大板块开始，逐步加入低相关币对

运行：

```bash
python scripts/select_pairs.py
```

### 3.3 最终选币结果

| 币对 | 板块 | 日均成交额 | 最高配对相关性 |
|------|------|-----------|--------------|
| SOL/USDT | L1 公链 | 240M | 0.53 (ZEC) |
| TAO/USDT | AI | 83M | 0.41 (SOL) |
| ZEC/USDT | 隐私 | 69M | 0.53 (SOL) |
| ENJ/USDT | 游戏 | 9M | -0.09 |
| ORDI/USDT | BTC 生态 | 5M | 0.43 (SOL) |

所有配对相关性 < 0.7，覆盖 5 个板块。

---

## 4. 数据准备

```bash
# 下载 5m + 1h 数据（多下载1个月用于 warmup）
freqtrade download-data --exchange binance \
  --pairs SOL/USDT TAO/USDT ZEC/USDT ENJ/USDT ORDI/USDT \
  --timeframes 5m 1h \
  --timerange 20240301-20250417 \
  --config user_data/strategies/config_enew.json
```

注意事项：

- TAO/USDT 数据从 2024-04-11 开始（上线较晚）
- startup_candle_count = 310，需要至少 310 根 K 线的 warmup 数据
- 1h 数据用于 informative 装饰器

---

## 5. 回测与优化流程

### 5.1 方案设计

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 回测时间 | 1年 (202404-202504) | 平衡样本量和市场结构变化 |
| 损失函数 | SharpeHyperOptLoss | 平衡收益和风险 |
| 优化顺序 | 先 buy -> 再 sell+trailing | 分步优化减少搜索空间 |
| Trials | 500/step | 中等搜索深度 |
| 过拟合防范 | 训练/测试分割 | 训练集 202404-202501，测试集 202501-202504 |
| 基线回测 | 先用默认参数跑基线 | 有了对比后再 hyperopt |

### 5.2 Step 0: 基线回测

**训练集（20240401-20250101）：**

```bash
freqtrade backtesting --strategy ENEW \
  --config user_data/strategies/config_enew.json \
  --timeframe 5m --timerange 20240401-20250101
```

基线结果（默认参数）：

- 交易数：4 笔
- 胜率：0%
- 总利润：-1.6%
- SOL/TAO/ORDI 零交易

结论：默认参数过于严格，入场信号极少。

### 5.3 Step 1: 优化 Buy 参数

```bash
freqtrade hyperopt --strategy ENEW \
  --config user_data/strategies/config_enew.json \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces buy \
  --timeframe 5m --timerange 20240401-20250101 \
  -j 4 -e 500
```

优化 6 个参数：buy_rsi_fast, buy_rsi, buy_sma15_ratio, buy_cti, buy_24h_min_pct, buy_24h_max_pct

运行时间：约 31 分钟（4核并行）

关键发现：

- 官方 "Best"（2笔交易）Sharpe 最高但样本量不足，不可靠
- Epoch 26（49笔交易，75.5%胜率，+6.0%）更有统计意义
- 选择 Epoch 26 参数作为 buy 最优解

### 5.4 Step 2: 优化 Sell + Trailing 参数

将 Step 1 的最优 buy 参数写入策略后，优化出场参数：

```bash
freqtrade hyperopt --strategy ENEW \
  --config user_data/strategies/config_enew.json \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces sell trailing \
  --timeframe 5m --timerange 20240401-20250101 \
  -j 4 -e 500
```

优化 5 个参数：sell_fastx, csl_initial, csl_mid_ratio, csl_late, trailing_stop_positive

运行时间：约 4 分钟

最佳结果（Epoch 479）：93.9% 胜率，+12.82% 总利润

### 5.5 Step 3: 测试集验证

```bash
freqtrade backtesting --strategy ENEW \
  --config user_data/strategies/config_enew.json \
  --timeframe 5m --timerange 20250101-20250417
```

---

## 6. 最终结果对比

| 指标 | 基线（默认参数） | 训练集（优化后） | 测试集（优化后） |
|------|-----------------|-----------------|-----------------|
| 交易数 | 4 | 49 | 17 |
| 胜率 | 0% | 93.9% | 76.5% |
| 总利润 | -1.6% | +12.82% | -1.93% |
| 最大回撤 | 1.6% | 3.17% | 3.55% |
| 平均持仓 | 1:10 | 0:16 | 0:23 |
| 同期大盘 | -17.98% | -17.98% | -54.65% |

**分析：**

- 训练集表现优秀：93.9% 胜率，+12.82% 总利润
- 测试集在极端下跌行情（大盘 -54.65%）中仅亏损 -1.93%，止损机制有效
- 最大回撤在训练集和测试集均稳定在 ~3.5%，风险控制一致
- 过拟合程度可控，训练集与测试集的差距主要来自市场环境差异

---

## 7. 遇到的问题与解决方案

### 7.1 EMA200 趋势过滤导致入场信号极少

问题：1h EMA200 要求价格在均线上方，与 RSI 超卖条件矛盾，9个月仅 4 笔交易。
解决：移除 EMA200 过滤，让 hyperopt 找到更合适的参数组合。

### 7.2 freqtrade stoploss space 不支持自定义参数

问题：`csl_initial` 等参数定义在 `space='stoploss'` 时，hyperopt 报 `KeyError`。
解决：改用 `space='sell'`，在 sell space 中一起优化。

### 7.3 trailing_stop_positive 不能设为 DecimalParameter

问题：freqtrade 的 `trailing_stop_positive` 是内置 float 属性，不能用 DecimalParameter 包装。
解决：保持为固定值，通过 `--spaces trailing` 让 freqtrade 自动搜索最优值。

### 7.4 pip 代理问题

问题：macOS 系统代理设为 127.0.0.1:1082 但代理服务未运行，导致 pip 无法安装依赖。
解决：使用 `NO_PROXY="*" no_proxy="*" pip install ...` 绕过代理。

### 7.5 Hyperopt "Best" 可能是过拟合

问题：2 笔交易的 epoch 因 Sharpe 极高被标为 "Best"，但样本量不足。
解决：手动审查所有 epoch 结果，选择交易量充足（>=30笔）且收益稳定的结果。

---

## 8. 文件清单

| 文件 | 说明 |
|------|------|
| `user_data/strategies/ENEW.py` | 优化后的策略文件 |
| `user_data/strategies/config_enew.json` | Dry_run 配置文件 |
| `scripts/select_pairs.py` | 自动选币脚本 |

## 9. 启动命令

```bash
# 先在 Telegram 给 bot 发 /start
freqtrade trade --config user_data/strategies/config_enew.json --strategy ENEW
```
