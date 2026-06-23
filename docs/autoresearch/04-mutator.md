# mutator.py — LLM 变异引擎深度解读

## 1. 文件职责

整个系统中最复杂、最频繁迭代的模块。负责：
1. 构建 LLM prompt（含历史实验、失败教训、约束提醒）
2. 调用多 provider LLM API（含 fallback 和重试）
3. 解析 LLM 响应（提取代码、分析、预期影响）
4. 自动修复 LLM 常见错误（scalar method、imports、API 兼容性）
5. AST 对比检测变化空间
6. 写入策略文件并继承父参数

## 2. Prompt 工程

### 2.1 System Prompt

`program.md` 作为 system prompt，定义了 LLM 的角色和约束：
- 你是量化策略架构师，不调参数
- 可以修改：indicators、entry/exit 逻辑框架、风控架构、hyperopt 参数定义
- 禁止修改：类名、接口签名、import 语句、硬编码最优值

### 2.2 User Prompt 构建 (`_build_prompt`)

Prompt 结构（从上到下）：

```
1. MUTATION FOCUS（如配置了）
2. Baseline metrics
3. Current best metrics（如有）
4. Recent experiments 表格（最近 20 条）
5. LEARNING NOTES（从失败实验总结）
6. CRITICAL WARNING（scalar method 错误提醒，如最近有）
7. REMINDER（每次必带的 scalar method 提醒）
8. Current strategy 源码
9. 输出格式要求
```

### 2.3 Learning Notes 生成 (`_build_learning_notes`)

这是 prompt 工程中最精妙的部分。它不是简单罗列失败记录，而是做**主题提取**和**模式识别**：

```python
theme_patterns = {
    "entry filters": ("entry filter", "populate_entry", "buy_", "entry logic"),
    "trend filters": ("trend filter", "ema", "adx", "falling knife", "counter-trend"),
    "volume gates": ("volume", "liquidity"),
    "stoploss/risk rewrite": ("stoploss", "trailing", "risk management"),
    "exit/roi rewrite": ("custom_exit", "exit", "roi", "sell"),
}
```

从最近 8 个失败实验中提取主题，按频率排序。然后生成针对性的建议：
- 如果 OOS trade count 暴跌：提醒不要加太宽的过滤门
- 如果 OOS PnL 低于 baseline：提醒不要牺牲绝对利润去追求 PF/DD

**设计意图**：让 LLM 从"统计显著性"角度学习，而不是凭感觉猜测。

### 2.4 History Table

```python
header = "| Iter | Score | IS PnL | IS Sharpe | IS Trades | OOS PnL | OOS Sharpe | OOS Trades | Status | Rejection |"
```

最近 20 条记录，按表格形式呈现，方便 LLM 快速扫描模式。

## 3. LLM 调用机制

### 3.1 多 Provider Fallback

```python
for provider in providers:
    for attempt in range(1, max_retries + 1):
        try:
            llm_result = future.result(timeout=request_timeout + 5)
            break
        except concurrent.futures.TimeoutError:
            time.sleep(2 ** attempt)  # 指数退避
        except Exception:
            time.sleep(2 ** attempt)
```

**重试策略**：
- 每个 provider 内部：最多 `max_retries` 次，指数退避（2s, 4s, 8s）
- Provider 之间：按 `ordered_providers()` 顺序 fallback
- 超时检测：用 `ThreadPoolExecutor` + `future.result(timeout=...)` 实现硬超时

### 3.2 httpx.Client 的创建与关闭

```python
with httpx.Client(timeout=httpx.Timeout(request_timeout)) as http_client:
    response = http_client.post(url, headers=..., json=payload)
```

**运维教训**：每次调用都新建 Client，因为 backtest 的 multiprocessing 会损坏连接池状态。

### 3.3 API 协议

所有 provider 走 Anthropic-compatible `/v1/messages`：
```python
payload = {
    "model": provider.model,
    "system": self.program_md,
    "messages": [{"role": "user", "content": prompt}],
    "temperature": ...,
    "max_tokens": ...,
}
headers = {
    "x-api-key": api_key,
    "anthropic-version": "2023-06-01",
}
```

响应解析：
```python
text = "".join(
    part.get("text", "")
    for part in data.get("content", [])
    if isinstance(part, dict) and part.get("type") == "text"
)
```

## 4. 响应解析

### 4.1 `_parse_response`

用正则提取三个部分：
```python
analysis = re.search(r"## ANALYSIS\s*\n(.*?)(?=\n## )", text, re.DOTALL)
code = re.search(r"## CHANGES\s*\n(.*?)(?=\n## |$)", text, re.DOTALL)
impact = re.search(r"## EXPECTED_IMPACT\s*\n(.*?)$", text, re.DOTALL)
```

代码提取：从 `## CHANGES` 中找最大的 Python code block。

### 4.2 如果没有 code block

如果 `## CHANGES` 里没有代码块，会回退到全局搜索最大的 code block。如果仍然没有，抛出 `ValueError`。

## 5. 自动修复系统

这是 mutator.py 中代码量最大的部分，也是运维踩坑的集中体现。

### 5.1 `_fix_scalar_methods`

**问题**：LLM 经常在 `custom_exit()`/`custom_stoploss()` 中写：
```python
current_candle["ema_50"] > current_candle["ema_50"].shift(10)  # 错误！
```

**修复**：AST NodeTransformer，在目标方法内将 `Subscript.shift()` 等调用替换为纯 Subscript：
```python
class _ScalarMethodFixer(ast.NodeTransformer):
    BANNED = {"shift", "rolling", "diff", "pct_change"}
    
    def visit_Call(self, node: ast.Call) -> ast.AST:
        if func.attr in self.BANNED and isinstance(func.value, ast.Subscript):
            return ast.copy_location(func.value, node)  # 删除方法调用，只保留下标
```

### 5.2 `_fix_imports`

**问题**：LLM 可能使用了 `IntParameter` 但忘记 import。

**修复**：检测代码中使用的 Parameter 类型，自动添加到 `from freqtrade.strategy import (...)` 中。支持多行和单行 import 格式。

### 5.3 `_fix_is_long`

**问题**：freqtrade 的 `LocalTrade` 没有 `is_long` 属性，只有 `is_short`。

**修复**：
```python
# not trade.is_long -> trade.is_short
# trade.is_long -> not trade.is_short
```

### 5.4 `_fix_entry_tag`

**问题**：freqtrade 将 `entry_tag` 重命名为 `enter_tag`。

**修复**：全局替换 `trade.entry_tag` -> `trade.enter_tag`。

### 5.5 `_fix_column_names`

**问题**：LLM 在 `populate_indicators()` 中定义了 `dataframe["adx"]`，但在 entry/exit 中使用了 `dataframe["ADX"]`（大小写不一致）。

**修复**：
1. 提取所有 `dataframe["xxx"] = ...` 的列名定义
2. 扫描所有 `dataframe["xxx"]` 引用
3. 如果引用的大小写与定义不一致，自动修正

### 5.6 `_fix_nbdevup_type`

**问题**：ta-lib 的 BBANDS 参数 `nbdevup`/`nbdevdn` 必须是 float，但 LLM 经常用 `IntParameter` 或整数常量。

**修复**：
- `IntParameter` -> `DecimalParameter`
- 整数常量如 `nbdevup=1` -> `nbdevup=1.0`

## 6. AST 变化空间检测

### 6.1 `detect_changed_spaces`

核心逻辑：对比原始代码和变异代码的 AST，判断哪些方法发生了变化，从而推断需要优化哪些 hyperopt space。

检测的方法：
- `populate_entry_trend` → `buy`
- `populate_exit_trend` → `sell`
- `populate_indicators` → `buy` + `sell`
- `custom_exit` → `sell`
- `custom_stoploss` → `sell` + `stoploss`
- `stoploss` 类属性 → `stoploss`
- hyperopt Parameter 定义变化 → 对应 space

### 6.2 AST 提取工具

```python
@staticmethod
def _extract_method_ast(source: str, method_name: str) -> ast.AST | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return item
```

### 6.3 AST 比较

```python
@staticmethod
def _methods_equal(a: ast.AST | None, b: ast.AST | None) -> bool:
    return ast.dump(a) == ast.dump(b)
```

**注意**：`ast.dump()` 比较的是 AST 结构，不是源代码文本。这意味着注释变化、空格变化不会被认为是代码变化，而逻辑等效但写法不同的代码（如 `a + b` vs `b + a`）会被认为不同。

## 7. 策略文件管理

### 7.1 `apply_mutation`

```python
def apply_mutation(self, source_code: str, iteration: int, output_dir: str) -> tuple[str, Path]:
    file_name = f"{self._base_name}_E{iteration:04d}"
    file_path = Path(output_dir) / f"{file_name}.py"
    
    # 重命名类名
    modified = re.sub(r"class\s+\w+", f"class {file_name}", source_code, count=1)
    file_path.write_text(modified)
    
    # 复制父策略参数
    self._copy_params_file(file_name, output_dir)
```

### 7.2 `_copy_params_file`

**设计意图**：新策略继承父策略的 hyperopt 参数，这样未优化的空间不会退回到默认值。

```python
def _copy_params_file(self, new_strategy_name: str, output_dir: str) -> None:
    parent_json = Path(output_dir) / f"{self._parent_strategy}.json"
    if not parent_json.exists():
        parent_json = Path(output_dir).parent / f"{self._parent_strategy}.json"
    
    if parent_json.exists():
        data = json.loads(parent_json.read_text())
        data["strategy_name"] = new_strategy_name
        new_json.write_text(json.dumps(data, indent=2))
```

**Fallback 逻辑**：如果输出目录中没有父参数文件，会到父目录（`strategies/`）查找。这支持从原始基线策略继承参数。

## 8. Cleanup

```python
def cleanup_strategy(self, file_path: Path) -> None:
    if file_path.exists():
        file_path.unlink()
    params_path = file_path.with_suffix(".json")
    if params_path.exists():
        params_path.unlink()
    pycache_dir = file_path.parent / "__pycache__"
    if pycache_dir.exists():
        for cached_file in pycache_dir.glob(f"{file_path.stem}.*.pyc"):
            cached_file.unlink()
```

**注意**：不仅删除 `.py` 和 `.json`，还清理 `__pycache__` 中的 `.pyc` 文件。这是为了避免 Python 缓存导致后续加载旧代码。
