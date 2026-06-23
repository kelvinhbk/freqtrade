# 运维踩坑记录与修复总结

> 以下内容来自 CLAUDE.md 中的运维记录，是实际运行中遇到的问题和修复方案，对理解代码中的某些"奇怪"设计至关重要。

## 1. LLM API 模型名称错误导致调用卡住

### 现象
mutator 记录 `Prompt length` 后无任何响应，进程 CPU 0%。

### 根因
配置中 `glm-5.1` 模型返回空内容，OpenAI client 的 httpx 连接池在空响应后进入损坏状态。

### 修复
`evolution_config_efuture.json` 中模型改为 `glm-4-plus`。

### 验证
```bash
.venv/bin/python -c "..."  # 直接测试 API 响应
```

### 代码体现
`mutator.py` 的 `_call_llm` 中每次新建 `httpx.Client`，并在 finally 中关闭，就是对此类连接池损坏问题的防御。

---

## 2. joblib semlock 泄漏导致新进程挂起

### 现象
`kill -9` 终止旧进程后，新进程启动但 LLM 调用永远不返回。

### 根因
loky/multiprocessing 的 semlock 未清理，macOS 信号量上限（~128-256）被耗尽。

### 修复
```bash
ipcs -s | tail -n +2 | awk '{print $2}' | while read id; do ipcrm -s "$id"; done
```

### 检查
```bash
ipcs -s | grep -c "^s"
```

### 代码体现
`evolve.py` 开头的 `multiprocessing.set_start_method("spawn")` 部分缓解了这个问题，因为 spawn 模式比 fork 模式更干净。

---

## 3. httpx 连接池在 backtest 后损坏

### 现象
前几次 LLM 调用正常，之后连续返回 "Connection error" 或 600 秒超时。

### 根因
OpenAI client 复用 httpx.Client，freqtrade backtest 内的 multiprocessing 影响连接池状态。

### 修复（`mutator.py`）
每次 `_call_llm()` 内新建 `httpx.Client(timeout=600)`：
```python
with httpx.Client(timeout=httpx.Timeout(request_timeout)) as http_client:
    response = http_client.post(...)
```

### 额外修复
- 单次超时从 600 秒缩短到 120 秒
- 重试循环同时捕获 `TimeoutError` 和 `ConnectionError`
- 3 次指数退避（2s, 4s, 8s）

---

## 4. 进程疯狂消耗迭代次数

### 现象
mutation/hyperopt 失败后 1 秒内跳到下一个 iteration，几分钟内消耗 15+ 次迭代。

### 修复（`evolve.py`）
`continue` 前添加 `time.sleep(5)`：
```python
except Exception as e:
    logger.error(f"Mutation failed: {e}")
    tracker.record_error(...)
    time.sleep(5)  # 给 API 限流恢复留出时间
    continue
```

---

## 5. LLM 生成代码的 freqtrade API 兼容性错误

### 现象
hyperopt 阶段大量 `AttributeError: 'LocalTrade' object has no attribute 'entry_tag'`

### 根因
freqtrade 将 `entry_tag` 重命名为 `enter_tag`，LLM 训练数据包含旧 API。

### 修复（`mutator.py`）
添加 `_fix_entry_tag()` 自动替换：
```python
def _fix_entry_tag(source_code: str) -> tuple[str, list[str]]:
    new_code, count = re.subn(r"\btrade\.entry_tag\b", "trade.enter_tag", source_code)
```

### 同类修复
- `_fix_is_long()`：`is_long` -> `not is_short`
- `_fix_scalar_methods()`：禁止 scalar 上调用 `.shift()`/`.rolling()`

---

## 6. 监控通知过滤建议

`grep --line-buffered` 过滤掉 `strategy_wrapper` 的逐行 ERROR，避免通知轰炸。

只监控：
```
Iteration [0-9]+|IMPROVEMENT|Regression|Hyperopt found|Mutation failed
```

---

## 7. 其他代码中的防御性设计

### 7.1 `mutator.py` 的多层自动修复

| 修复函数 | 问题 | 层级 |
|---------|------|------|
| `_fix_scalar_methods` | scalar 上调用 Series 方法 | AST 自动修复 |
| `_fix_imports` | 忘记 import Parameter 类型 | 正则自动修复 |
| `_fix_is_long` | 使用不存在的 `trade.is_long` | 正则自动修复 |
| `_fix_entry_tag` | 使用旧 API `trade.entry_tag` | 正则自动修复 |
| `_fix_column_names` | 列名大小写不一致 | 正则自动修复 |
| `_fix_nbdevup_type` | BBANDS 参数类型错误 | 正则自动修复 |

### 7.2 `safety.py` 的 L1 检查

静态检查作为 LLM 代码的第一道防线：
- AST 语法检查
- 危险函数检测
- scalar method 检测
- Series 布尔条件检测
- `trade.is_long` 检测

### 7.3 `evolve.py` 的异常处理模式

每个阶段失败后：
1. 记录到 tracker（JSONL 持久化）
2. cleanup 临时文件
3. `time.sleep(5)` 限流保护
4. `continue` 进入下一轮

这个模式确保了系统的**自愈能力**：即使连续失败，也不会崩溃，而是等待条件恢复后继续尝试。
