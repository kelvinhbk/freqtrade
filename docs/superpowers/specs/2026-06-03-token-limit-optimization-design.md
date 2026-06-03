# Token Limit Optimization for Autoresearch Evolution

**Date**: 2026-06-03
**Status**: Draft
**Scope**: `user_data/autoresearch/mutator.py`, `config.py`

## Problem

During autoresearch strategy evolution, LLM API calls fail with context window limit errors:

```
API Error: The model has reached its context window limit.
API Error: 400 Invalid request: Your request exceeded model token limit: 262144 (requested: 292352)
```

This happens across multiple providers (GLM, Kimi, DeepSeek). The issue appears mid-iteration as history records accumulate and strategy code grows through successive mutations.

## Root Cause

The prompt sent to the LLM grows unboundedly:

1. **History table** (`_build_history_table`): shows last 20 experiment records
2. **Learning notes** (`_build_learning_notes`): analyzes last 8 failures with detailed metrics
3. **Strategy source code**: grows as mutations add indicators, parameters, and conditions
4. **Blacklist rules**: accumulate over time
5. **No pre-send validation**: the prompt is built and sent without checking its size

## Design

### Approach: Token Estimation + Tiered Trimming + Error Recovery

Combination of proactive trimming (prevent over-limit) and reactive error recovery (handle API errors gracefully).

---

### 1. Configuration: `max_context_tokens` per provider

Add an optional field to `LLMProviderConfig`:

```python
@dataclass(frozen=True)
class LLMProviderConfig:
    # ... existing fields ...
    max_context_tokens: int | None = None  # context window size for this model
```

Default value: `200000` (200K tokens) when not specified.

JSON config usage:

```json
{
  "glm": {
    "base_url": "https://open.bigmodel.cn/api/anthropic",
    "api_key_env": "GLM_API_KEY",
    "model": "glm-5.1",
    "max_context_tokens": 262144
  },
  "kimi": {
    "base_url": "https://api.kimi.com/coding",
    "api_key_env": "KIMI_API_KEY",
    "model": "kimi-2.6",
    "max_context_tokens": 131072
  }
}
```

Parsing in `_parse_llm_providers()`:

```python
max_context_tokens=provider_data.get("max_context_tokens"),
```

---

### 2. Token Estimation

New function in `mutator.py`:

```python
def estimate_tokens(text: str) -> int:
    """Conservative token count estimate for mixed code/Chinese text."""
    if not text:
        return 0
    # Count CJK characters (each ~1.5 tokens)
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    non_cjk = len(text) - cjk
    return int(cjk * 1.5 + non_cjk / 3.5) + 100  # +100 buffer
```

---

### 3. Tiered Prompt Trimming

New function `trim_prompt()` with the following priority (trim lowest-value content first):

| Tier | Action | Estimated Savings |
|------|--------|-------------------|
| 0 | No trimming (prompt fits within budget) | 0% |
| 1 | History table: 20 -> 10 records | ~50% history tokens |
| 2 | History table: 10 -> 5 records; learning notes: 8 -> 4 failures | ~75% history tokens |
| 3 | History table: 5 -> 3 records; learning notes: 2 failures; program.md examples stripped | ~85% history + ~30% system prompt |
| 4 | Minimal mode: metrics + 3 history rows + strategy skeleton | Maximum savings |

**Budget calculation**:

```python
budget = provider.max_context_tokens - max_tokens - 2048  # safety buffer
```

**Trimming flow in `propose_mutation()`**:

```
prompt = _build_prompt(...)
prompt_tokens = estimate_tokens(system_prompt + prompt)
budget = effective_max_context - effective_max_tokens - 2048

if prompt_tokens > budget:
    for tier in [1, 2, 3, 4]:
        prompt = trim_prompt(prompt, tier)
        if estimate_tokens(system + prompt) <= budget:
            break
```

**Implementation**: `trim_prompt()` is a two-phase function:

Phase 1 (tiers 1-2): Rebuild prompt with adjusted parameters:
- Add `max_history` and `max_failures` parameters to `_build_prompt()`
- `_build_history_table(records, max_rows=20)`
- `_build_learning_notes(records, max_failures=8)`

Phase 2 (tiers 3-4): Post-process the built prompt string:
- Tier 3: Strip example code blocks from `program_md` via regex
- Tier 4: Replace full strategy source with `_extract_code_skeleton()` output

---

### 4. Strategy Code Skeleton Extraction (Tier 4 only)

When tier 4 trimming is needed, extract a skeleton of the strategy:

- Keep: imports, class definition, class attributes (Parameter definitions)
- Keep: `populate_indicators()` method body in full
- Keep: method signatures for all other methods
- Replace method bodies with `# [body preserved from previous version]`

New function:

```python
def _extract_code_skeleton(source_code: str) -> str:
    """Extract strategy skeleton: imports + class attrs + indicators + method signatures."""
```

Uses `ast` module to parse the source, walk the class body, and reconstruct a skeleton.

---

### 5. API Error Recovery

In `_call_llm()`, catch token limit errors:

```python
# Recognize token limit errors from response
if response.status_code == 400:
    error_text = response.text.lower()
    if any(kw in error_text for kw in ("token limit", "context window", "exceeded")):
        raise TokenLimitError(provider.name, provider.model, ...)
```

New exception class:

```python
class TokenLimitError(Exception):
    def __init__(self, provider: str, model: str, requested: int, limit: int):
        self.provider = provider
        self.model = model
        self.requested = requested
        self.limit = limit
```

In `propose_mutation()`, the retry loop handles `TokenLimitError`:

1. Catch `TokenLimitError` from current provider
2. Re-try with maximal trimming (tier 4) on same provider
3. If still fails, fall through to next provider
4. If all providers fail, raise the error (evolve.py catches it and continues to next iteration)

---

### 6. Files Changed

| File | Changes |
|------|---------|
| `config.py` | Add `max_context_tokens` field to `LLMProviderConfig`; add parsing in `_parse_llm_providers()` |
| `mutator.py` | Add `estimate_tokens()`, `trim_prompt()`, `_extract_code_skeleton()`, `TokenLimitError`; modify `_build_prompt()` to accept `max_history`/`max_failures`; modify `propose_mutation()` to check budget and trim; modify `_call_llm()` to raise `TokenLimitError` |

No changes to `evolve.py`, `tracker.py`, `program.md`, or any config JSON files (users opt in by adding `max_context_tokens` to their provider configs).

---

### 7. Testing

- Unit test `estimate_tokens()` with code, Chinese text, and mixed inputs
- Unit test `_extract_code_skeleton()` with a sample strategy
- Unit test `trim_prompt()` at each tier level
- Integration test: mock LLM call with known prompt size, verify trimming kicks in
- Verify `TokenLimitError` is raised and caught correctly in the retry loop
