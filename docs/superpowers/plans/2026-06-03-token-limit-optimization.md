# Token Limit Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent autoresearch LLM calls from exceeding model context windows by estimating token counts, tiered prompt trimming, and graceful error recovery.

**Architecture:** Add `max_context_tokens` to provider config, estimate prompt tokens before sending, trim prompt in tiers when over budget, catch token-limit API errors and retry with trimmed prompt.

**Tech Stack:** Python 3.11+, existing `ast`/`re` stdlib, httpx, pytest

**Spec:** `docs/superpowers/specs/2026-06-03-token-limit-optimization-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `user_data/autoresearch/config.py` | Modify | Add `max_context_tokens` field + `effective_max_context_tokens()` |
| `user_data/autoresearch/mutator.py` | Modify | Add `estimate_tokens`, `TokenLimitError`, `_extract_code_skeleton`, trimming in `_build_*` functions, budget check in `propose_mutation`, error recovery in `_call_llm` |
| `user_data/autoresearch/tests/test_autoresearch_core.py` | Modify | Add tests for all new functions |

---

### Task 1: Add `max_context_tokens` to config

**Files:**
- Modify: `user_data/autoresearch/config.py:21-34` (LLMProviderConfig dataclass)
- Modify: `user_data/autoresearch/config.py:46-61` (effective_* methods)
- Modify: `user_data/autoresearch/config.py:395-407` (_parse_llm_providers body)
- Test: `user_data/autoresearch/tests/test_autoresearch_core.py`

- [ ] **Step 1: Write the failing test**

Append to `test_autoresearch_core.py`:

```python
def test_provider_config_max_context_tokens_default() -> None:
    provider = LLMProviderConfig(
        name="test",
        base_url="http://x",
        api_key_env="K",
        model="m",
    )
    assert provider.max_context_tokens is None


def test_provider_config_effective_max_context_tokens() -> None:
    provider = LLMProviderConfig(
        name="test",
        base_url="http://x",
        api_key_env="K",
        model="m",
        max_context_tokens=262144,
    )
    defaults = LLMConfig()
    assert provider.effective_max_context_tokens(defaults) == 262144

    no_override = LLMProviderConfig(
        name="test2",
        base_url="http://x",
        api_key_env="K",
        model="m2",
    )
    assert no_override.effective_max_context_tokens(defaults) == 200000


def test_load_config_parses_max_context_tokens(tmp_path: Path) -> None:
    config_path = tmp_path / "evolution.json"
    config_path.write_text(
        """
{
  "llm": {
    "providers": {
      "glm": {
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "api_key_env": "GLM_API_KEY",
        "model": "glm-5.1",
        "max_context_tokens": 262144
      }
    }
  }
}
"""
    )
    config = load_evolution_config(config_path)
    providers = config.llm.ordered_providers()
    assert providers[0].max_context_tokens == 262144
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py::test_provider_config_max_context_tokens_default user_data/autoresearch/tests/test_autoresearch_core.py::test_provider_config_effective_max_context_tokens user_data/autoresearch/tests/test_autoresearch_core.py::test_load_config_parses_max_context_tokens -v`
Expected: FAIL

- [ ] **Step 3: Add field and method to `LLMProviderConfig` in `config.py`**

After `extra_body` field add `max_context_tokens: int | None = None`.
After `effective_max_retries` method add `effective_max_context_tokens` method returning `self.max_context_tokens if not None else 200000`.
In `_parse_llm_providers` body add `max_context_tokens=provider_data.get("max_context_tokens"),`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add user_data/autoresearch/config.py user_data/autoresearch/tests/test_autoresearch_core.py
git commit -m "feat(autoresearch): add max_context_tokens to LLMProviderConfig"
```

---

### Task 2: Add `estimate_tokens` and `TokenLimitError`

**Files:**
- Modify: `user_data/autoresearch/mutator.py` (after logger definition, line 17)
- Test: `user_data/autoresearch/tests/test_autoresearch_core.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_autoresearch_core.py`:

```python
def test_estimate_tokens_empty_string() -> None:
    from mutator import estimate_tokens
    assert estimate_tokens("") == 0


def test_estimate_tokens_code() -> None:
    from mutator import estimate_tokens
    code = "def foo(x: int) -> int:\n    return x + 1\n"
    tokens = estimate_tokens(code)
    assert tokens > 0
    assert tokens < len(code)


def test_estimate_tokens_chinese() -> None:
    from mutator import estimate_tokens
    text = "这是一个测试文本，包含中文字符。"
    tokens = estimate_tokens(text)
    assert tokens > 0


def test_estimate_tokens_mixed() -> None:
    from mutator import estimate_tokens
    text = "def foo():\n    # 这是一个注释\n    return 42\n"
    tokens = estimate_tokens(text)
    assert tokens > 0


def test_token_limit_error_attributes() -> None:
    from mutator import TokenLimitError
    err = TokenLimitError("glm", "glm-5.1", 292352, 262144)
    assert err.provider == "glm"
    assert err.model == "glm-5.1"
    assert err.requested == 292352
    assert err.limit == 262144
    assert "glm" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py::test_estimate_tokens_empty_string -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Add `estimate_tokens` and `TokenLimitError` to `mutator.py`**

After logger (line 17), add `TokenLimitError` class and `estimate_tokens` function. See spec section 2 and 5 for exact code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add user_data/autoresearch/mutator.py user_data/autoresearch/tests/test_autoresearch_core.py
git commit -m "feat(autoresearch): add estimate_tokens and TokenLimitError"
```

---

### Task 3: Add `max_rows` to `_build_history_table` and `max_failures` to `_build_learning_notes`

**Files:**
- Modify: `user_data/autoresearch/mutator.py:28-60` (`_build_history_table`)
- Modify: `user_data/autoresearch/mutator.py:91-192` (`_build_learning_notes`)
- Test: `user_data/autoresearch/tests/test_autoresearch_core.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_history_table_max_rows_limits_output() -> None:
    from mutator import _build_history_table

    records_list = [
        record(
            iteration=i,
            status="regression",
            in_sample=metrics(sharpe=1.0, profit_factor=1.0, calmar=5, win_rate=0.5, trades=100, profit=10),
            out_of_sample=metrics(sharpe=0.5, profit_factor=0.9, calmar=2, win_rate=0.45, trades=20, profit=-5),
        )
        for i in range(20)
    ]
    full = _build_history_table(records_list)
    limited = _build_history_table(records_list, max_rows=5)
    assert full.count("|") > limited.count("|")
    assert len(limited.strip().split("\n")) == 7


def test_build_learning_notes_max_failures_limits_output() -> None:
    from mutator import _build_learning_notes

    baseline_is = metrics(sharpe=5.0, profit_factor=1.3, calmar=18, win_rate=0.57, trades=1400, profit=920)
    baseline_oos = metrics(sharpe=3.1, profit_factor=1.35, calmar=45, win_rate=0.57, trades=135, profit=78)
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
    ] + [
        record(
            iteration=i, status="regression", in_sample=baseline_is,
            out_of_sample=metrics(sharpe=-0.5, profit_factor=0.45, calmar=-10, win_rate=0.75, trades=9, profit=-17),
            reason="OOS trades too low", analysis="Added EMA trend filter",
        )
        for i in range(1, 9)
    ]
    full = _build_learning_notes(history)
    limited = _build_learning_notes(history, max_failures=3)
    assert "LESSONS FROM RECENT EXPERIMENTS" in limited
    assert limited.count("- Iter") <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py::test_build_history_table_max_rows_limits_output -v`
Expected: FAIL (unexpected keyword argument)

- [ ] **Step 3: Add `max_rows` param to `_build_history_table` and `max_failures` to `_build_learning_notes`**

Change signature of `_build_history_table` to `(records, max_rows=20)`, use `records[-max_rows:]`.
Change signature of `_build_learning_notes` to `(records, max_failures=8)`, use `failures[-max_failures:]` and `failures[-min(5, max_failures):]`.

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add user_data/autoresearch/mutator.py user_data/autoresearch/tests/test_autoresearch_core.py
git commit -m "feat(autoresearch): add max_rows and max_failures params to prompt builders"
```

---

### Task 4: Add `_build_prompt` trim parameters and `_extract_code_skeleton`

**Files:**
- Modify: `user_data/autoresearch/mutator.py:195-286` (`_build_prompt`)
- Modify: `user_data/autoresearch/mutator.py` (new `_extract_code_skeleton` function)
- Test: `user_data/autoresearch/tests/test_autoresearch_core.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_extract_code_skeleton_preserves_imports_and_indicators() -> None:
    from mutator import _extract_code_skeleton

    source = """
from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

class TestStrategy(IStrategy):
    buy_rsi = IntParameter(10, 40, default=20, space="buy")
    stoploss = -0.10

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["rsi"] < self.buy_rsi.value),
            "enter_long",
        ] = 1
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        current_candle = dataframe.iloc[-1]
        if current_candle["rsi"] > 80:
            return "rsi_exit"
        return None
"""
    skeleton = _extract_code_skeleton(source)
    assert "from freqtrade.strategy import IStrategy" in skeleton
    assert "class TestStrategy(IStrategy):" in skeleton
    assert "buy_rsi = IntParameter" in skeleton
    assert "stoploss = -0.10" in skeleton
    assert "populate_indicators" in skeleton
    assert 'dataframe["rsi"]' in skeleton
    assert "[body preserved" in skeleton
    assert "enter_long" not in skeleton
    assert "rsi_exit" not in skeleton


def test_build_prompt_with_trim_params() -> None:
    baseline_is = metrics(sharpe=2.0, profit_factor=1.4, calmar=10, win_rate=0.6, trades=200, profit=100)
    baseline_oos = metrics(sharpe=1.0, profit_factor=1.2, calmar=5, win_rate=0.55, trades=50, profit=30)
    history = [
        record(iteration=0, status="baseline", in_sample=baseline_is, out_of_sample=baseline_oos),
    ] + [
        record(iteration=i, status="regression", in_sample=baseline_is,
               out_of_sample=metrics(sharpe=0.5, profit_factor=0.9, calmar=2, win_rate=0.45, trades=20, profit=-5))
        for i in range(1, 15)
    ]
    full = _build_prompt(EvolutionConfig(llm=LLMConfig()), BASE_SOURCE, baseline_is, None, history)
    trimmed = _build_prompt(EvolutionConfig(llm=LLMConfig()), BASE_SOURCE, baseline_is, None, history,
                            max_history=5, max_failures=3)
    assert len(full) > len(trimmed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py::test_extract_code_skeleton_preserves_imports_and_indicators -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Add `_extract_code_skeleton` function using `ast` module**

Uses `ast.parse`, walks top-level nodes for imports, finds the class, keeps class attrs + `populate_indicators` body + method signatures with `# [body preserved from previous version]`.

- [ ] **Step 4: Add `max_history` and `max_failures` params to `_build_prompt`**

Change signature to include `max_history: int = 20, max_failures: int = 8`. Pass through to `_build_history_table` and `_build_learning_notes`.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add user_data/autoresearch/mutator.py user_data/autoresearch/tests/test_autoresearch_core.py
git commit -m "feat(autoresearch): add code skeleton extraction and prompt trim params"
```

---

### Task 5: Add token budget check and tiered trimming to `propose_mutation`

**Files:**
- Modify: `user_data/autoresearch/mutator.py` (`propose_mutation` method)
- Test: `user_data/autoresearch/tests/test_autoresearch_core.py`

- [ ] **Step 1: Write the failing test**

Test uses `max_context_tokens=1000` (tiny budget) to force trimming, mocks httpx.Client.post, verifies `_build_prompt` called with `max_history < 20`.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (no trimming logic)

- [ ] **Step 3: Add budget check and tiered trimming in `propose_mutation`**

After `_build_prompt()`, compute budget per provider. If over budget, loop through 4 tiers of trimming kwargs, rebuild prompt, break when fits. Tier 4 also applies `_extract_code_skeleton`.

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add user_data/autoresearch/mutator.py user_data/autoresearch/tests/test_autoresearch_core.py
git commit -m "feat(autoresearch): add token budget check and tiered prompt trimming"
```

---

### Task 6: Add token limit error detection and retry

**Files:**
- Modify: `user_data/autoresearch/mutator.py` (`_call_llm` and retry loop)
- Test: `user_data/autoresearch/tests/test_autoresearch_core.py`

- [ ] **Step 1: Write the failing test**

Test mocks httpx.Client.post to return 400 with "token limit" on first call, 200 on second. Verifies `propose_mutation` succeeds after retry.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (httpx.HTTPStatusError not caught as TokenLimitError)

- [ ] **Step 3: Modify `_call_llm` to detect token limit in 400 responses**

Before `response.raise_for_status()`, check for status 400 with "token limit"/"context window"/"exceeded" keywords, raise `TokenLimitError`.

- [ ] **Step 4: Add `TokenLimitError` catch in retry loop**

After TimeoutError handler, before generic Exception handler, add `except TokenLimitError` that breaks to next provider.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kelvin/projects/freqtrade && python -m pytest user_data/autoresearch/tests/test_autoresearch_core.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add user_data/autoresearch/mutator.py user_data/autoresearch/tests/test_autoresearch_core.py
git commit -m "feat(autoresearch): detect token limit API errors and retry with trimmed prompt"
```

---

## Self-Review

**Spec coverage:**
- Section 1 (max_context_tokens): Task 1
- Section 2 (estimate_tokens): Task 2
- Section 3 (tiered trimming): Task 3 + Task 4 + Task 5
- Section 4 (code skeleton): Task 4
- Section 5 (API error recovery): Task 6

**Placeholder scan:** No TBD/TODO/placeholders.

**Type consistency:** `estimate_tokens(str) -> int`, `TokenLimitError(provider, model, requested, limit)`, `effective_max_context_tokens(defaults) -> int`, `_build_history_table(records, max_rows=20)`, `_build_learning_notes(records, max_failures=8)`, `_build_prompt(..., max_history=20, max_failures=8)` -- all consistent.
