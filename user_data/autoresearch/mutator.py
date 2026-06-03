"""
AutoResearch for Freqtrade - LLM-Driven Strategy Mutator

Uses Anthropic-compatible API providers to propose strategy mutations.
"""

import ast
import concurrent.futures
import logging
import re
from pathlib import Path

from config import EvolutionConfig, LLMProviderConfig
from tracker import BacktestMetrics, ExperimentRecord, MutationRecord, StrategySnapshot


logger = logging.getLogger(__name__)


class TokenLimitError(Exception):
    """Raised when an LLM request exceeds the model's context window."""

    def __init__(
        self, provider: str, model: str, requested: int, limit: int
    ) -> None:
        self.provider = provider
        self.model = model
        self.requested = requested
        self.limit = limit
        super().__init__(
            f"Token limit exceeded for {provider}/{model}: "
            f"requested {requested}, limit {limit}"
        )


def estimate_tokens(text: str) -> int:
    """Conservative token count estimate for mixed code/Chinese text."""
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    non_cjk = len(text) - cjk
    return int(cjk * 1.5 + non_cjk / 3.5) + 100


PROGRAM_MD_PATH = Path(__file__).parent / "program.md"


def _load_program_md() -> str:
    if PROGRAM_MD_PATH.exists():
        return PROGRAM_MD_PATH.read_text()
    return "You are a crypto trading strategy researcher."


def _build_history_table(records: list[ExperimentRecord], max_rows: int = 20) -> str:
    if not records:
        return "No previous experiments."
    header = (
        "| Iter | Score | IS PnL | IS Sharpe | IS Trades | OOS PnL | "
        "OOS Sharpe | OOS Trades | Status | Rejection |"
    )
    sep = (
        "|------|-------|--------|-----------|-----------|---------|"
        "------------|------------|--------|-----------|"
    )
    rows = []
    for r in records[-max_rows:]:
        is_m = r.in_sample
        oos_m = r.out_of_sample
        reason = (r.rejection_reason or "")[:80].replace("\n", " ")
        if is_m is None:
            row = (
                f"| {r.iteration} | - | - | - | - | - | - | - | "
                f"{r.status} | {reason} |"
            )
        else:
            oos_pnl = f"{oos_m.profit_total_abs:.1f}" if oos_m else "-"
            oos_sharpe = f"{oos_m.sharpe:.2f}" if oos_m else "-"
            oos_trades = f"{oos_m.total_trades}" if oos_m else "-"
            row = (
                f"| {r.iteration} | {r.composite_score:.3f} | "
                f"{is_m.profit_total_abs:.1f} | {is_m.sharpe:.2f} | "
                f"{is_m.total_trades} | {oos_pnl} | {oos_sharpe} | "
                f"{oos_trades} | {r.status} | {reason} |"
            )
        rows.append(row)
    return "\n".join([header, sep] + rows)


def _ratio(value: float, baseline: float) -> str:
    if baseline == 0:
        return "n/a"
    return f"{value / baseline:.2f}x"


def _extract_failure_themes(record: ExperimentRecord) -> list[str]:
    text = " ".join([
        record.mutation.description,
        record.mutation.llm_analysis,
        record.mutation.llm_expected_impact,
        record.rejection_reason or "",
    ]).lower()
    theme_patterns = {
        "entry filters": ("entry filter", "populate_entry", "buy_", "entry logic"),
        "trend filters": ("trend filter", "ema", "adx", "falling knife", "counter-trend"),
        "volume gates": ("volume", "liquidity"),
        "stoploss/risk rewrite": ("stoploss", "trailing", "risk management"),
        "exit/roi rewrite": ("custom_exit", "exit", "roi", "sell"),
    }
    themes = [
        theme
        for theme, patterns in theme_patterns.items()
        if any(pattern in text for pattern in patterns)
    ]
    return themes


def _build_learning_notes(records: list[ExperimentRecord], max_failures: int = 8) -> str:
    """Summarize measured lessons from recent failed experiments."""
    failures = [
        r for r in records
        if r.status in {"rejected", "regression", "error"} and r.iteration > 0
    ]
    if not failures:
        return (
            "No rejected or regressed candidates are available yet. Use the baseline "
            "metrics and the run focus, then make the smallest mutation likely to improve OOS."
        )

    baseline = next(
        (
            r for r in records
            if r.status == "baseline" and r.out_of_sample is not None
        ),
        None,
    )
    baseline_oos = baseline.out_of_sample if baseline else None

    lines = [
        "LESSONS FROM RECENT EXPERIMENTS:",
        "Use these measured failures as negative examples. Do not write boilerplate; "
        "choose a mutation that directly addresses the recurring failure pattern.",
    ]

    theme_counts: dict[str, int] = {}
    for record in failures[-max_failures:]:
        for theme in _extract_failure_themes(record):
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

    if theme_counts:
        ranked_themes = sorted(theme_counts.items(), key=lambda item: item[1], reverse=True)
        theme_text = ", ".join(f"{theme} ({count}x)" for theme, count in ranked_themes[:5])
        lines.append(f"Repeated failed proposal themes: {theme_text}.")

    oos_failures = [r for r in failures if r.out_of_sample is not None]
    if baseline_oos and oos_failures:
        pnl_values = [r.out_of_sample.profit_total_abs for r in oos_failures if r.out_of_sample]
        trade_values = [r.out_of_sample.total_trades for r in oos_failures if r.out_of_sample]
        if pnl_values and trade_values:
            lines.append(
                "Recent failed OOS range vs baseline: "
                f"PnL {min(pnl_values):.1f}..{max(pnl_values):.1f} "
                f"(baseline {baseline_oos.profit_total_abs:.1f}); "
                f"trades {min(trade_values)}..{max(trade_values)} "
                f"(baseline {baseline_oos.total_trades})."
            )
        if any(
            r.out_of_sample.total_trades < baseline_oos.total_trades * 0.4
            for r in oos_failures
        ):
            lines.append(
                "Main measured failure: OOS trade opportunity collapsed. The next mutation "
                "should avoid broad gates that remove most baseline opportunities."
            )
        if any(
            r.out_of_sample.profit_total_abs < baseline_oos.profit_total_abs
            for r in oos_failures
        ):
            lines.append(
                "Main measured failure: OOS PnL stayed below baseline. Do not chase PF/DD "
                "improvement if it sacrifices absolute OOS profit."
            )

    lines.append("Recent failed candidates:")
    for record in failures[-min(5, max_failures):]:
        pieces = [
            f"Iter {record.iteration} {record.status}",
            f"reason={record.rejection_reason or 'score did not improve'}",
        ]
        if baseline_oos and record.out_of_sample:
            pnl_ratio = _ratio(
                record.out_of_sample.profit_total_abs,
                baseline_oos.profit_total_abs,
            )
            trade_ratio = _ratio(
                record.out_of_sample.total_trades,
                baseline_oos.total_trades,
            )
            pieces.extend([
                (
                    f"OOS PnL={record.out_of_sample.profit_total_abs:.1f} "
                    f"({pnl_ratio})"
                ),
                (
                    f"OOS trades={record.out_of_sample.total_trades} "
                    f"({trade_ratio})"
                ),
                f"OOS Sharpe={record.out_of_sample.sharpe:.2f}",
            ])
        themes = _extract_failure_themes(record)
        if themes:
            pieces.append(f"themes={', '.join(themes[:4])}")
        lines.append("- " + "; ".join(pieces))

    lines.append(
        "For the next proposal, explain which failed pattern you are avoiding and how "
        "the new mutation differs from the recent rejected/regressed attempts."
    )
    return "\n".join(lines)


def _build_prompt(
    config: EvolutionConfig,
    source_code: str,
    baseline_is: BacktestMetrics,
    best_snapshot: StrategySnapshot | None,
    history: list[ExperimentRecord],
    blacklist_rules: list | None = None,
    max_history: int = 20,
    max_failures: int = 8,
) -> str:
    best_is = best_snapshot.in_sample if best_snapshot else baseline_is
    lines = []

    # Inject blacklist rules as hard constraints at the very top
    if blacklist_rules:
        from blacklist import inject_into_prompt
        blacklist_header = inject_into_prompt("", blacklist_rules)
        lines.append(blacklist_header)

    if config.mutation_focus or config.mutation_constraints:
        lines.append("MUTATION FOCUS - MUST FOLLOW BEFORE ANY OTHER ADVICE:")
        lines.append(
            "This run-specific focus overrides the general system guidance. "
            "If there is a conflict, follow this section."
        )
        if config.mutation_focus:
            lines.append(config.mutation_focus.strip())
        for constraint in config.mutation_constraints:
            lines.append(f"- {constraint}")
        lines.append("")

    lines.extend([
        "Baseline metrics:",
        f"  Sharpe: {baseline_is.sharpe:.3f}  PF: {baseline_is.profit_factor:.3f}  "
        f"Calmar: {baseline_is.calmar:.3f}  DD: {baseline_is.max_drawdown_account:.1%}  "
        f"WinRate: {baseline_is.win_rate:.1%}  Trades: {baseline_is.total_trades}  "
        f"PnL: {baseline_is.profit_total_abs:.2f}",
    ])
    if best_snapshot and best_snapshot.generation > 0:
        lines.append(
            f"Current best (gen {best_snapshot.generation}): "
            f"Sharpe={best_is.sharpe:.3f}  PF={best_is.profit_factor:.3f}  "
            f"PnL={best_is.profit_total_abs:.2f}"
        )
    lines.append("")
    lines.append("Recent experiments:")
    lines.append(_build_history_table(history, max_rows=max_history))
    lines.append("")
    lines.append(_build_learning_notes(history, max_failures=max_failures))
    lines.append("")

    # Inject critical warning if LLM keeps making the same scalar-method mistake
    scalar_method_rejections = [
        r for r in history[-5:]
        if r.status == "rejected"
        and r.rejection_reason
        and "scalar value cannot use" in r.rejection_reason
    ]
    if scalar_method_rejections:
        lines.append("CRITICAL WARNING - DO NOT IGNORE:")
        lines.append(
            f"Your previous {len(scalar_method_rejections)} mutation(s) were REJECTED "
            "because you called .shift(), .rolling(), .diff(), or .pct_change() on "
            "SCALAR values in custom_exit() or custom_stoploss()."
        )
        lines.append("")

    # ALWAYS include this reminder (LLMs often miss constraints in system prompt)
    lines.append("REMINDER - COMMON MISTAKE TO AVOID:")
    lines.append(
        "In custom_exit() and custom_stoploss(), current_candle = dataframe.iloc[-1] "
        "is a SINGLE SCALAR VALUE. You CANNOT call .shift(), .rolling(), .diff(), "
        ".pct_change() on it. Pre-compute ALL time-series in populate_indicators()."
    )
    lines.append("BAD: current_candle['ema_50'] > current_candle['ema_50'].shift(10)")
    lines.append(
        "GOOD: Pre-compute 'trend_up' in populate_indicators(), "
        "then use current_candle['trend_up']"
    )
    lines.append("")

    lines.append("Current strategy:")
    lines.append("```python")
    lines.append(source_code)
    lines.append("```")
    lines.append("")
    lines.append(
        "Propose ONE specific mutation. Respond in this format:\n\n"
        "## ANALYSIS\nYour reasoning. Reference the recent experiment lessons and explain "
        "why this mutation is different from the failed patterns.\n\n"
        "## CHANGES\n```python\nComplete modified class code.\n```\n\n"
        "## EXPECTED_IMPACT\nBrief prediction."
    )
    return "\n".join(lines)


def _parse_response(text: str) -> tuple[str, str, str]:
    """Parse LLM response into (analysis, code, expected_impact)."""
    analysis = ""
    code = ""
    impact = ""

    m = re.search(r"## ANALYSIS\s*\n(.*?)(?=\n## )", text, re.DOTALL)
    if m:
        analysis = m.group(1).strip()

    m = re.search(r"## EXPECTED_IMPACT\s*\n(.*?)$", text, re.DOTALL)
    if m:
        impact = m.group(1).strip()

    m = re.search(r"## CHANGES\s*\n(.*?)(?=\n## |$)", text, re.DOTALL)
    if m:
        blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", m.group(1), re.DOTALL)
        if blocks:
            code = max(blocks, key=len)

    if not code:
        blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if blocks:
            code = max(blocks, key=len)

    return analysis, code, impact


class _ScalarMethodFixer(ast.NodeTransformer):
    """Remove .shift(), .rolling(), .diff(), .pct_change() calls on scalar subscripts."""

    BANNED = {"shift", "rolling", "diff", "pct_change"}

    def __init__(self) -> None:
        self._in_target_method = False
        self.fixes: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:  # type: ignore[override]
        if node.name in {"custom_exit", "custom_stoploss"}:
            self._in_target_method = True
            node = self.generic_visit(node)  # type: ignore[assignment]
            self._in_target_method = False
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:  # type: ignore[override]
        if not self._in_target_method:
            return self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in self.BANNED:
            value = func.value
            if isinstance(value, ast.Subscript):
                self.fixes.append(func.attr)
                # Replace the entire Call with just the Subscript
                return ast.copy_location(value, node)
        return self.generic_visit(node)


def _fix_scalar_methods(source_code: str) -> tuple[str, list[str]]:
    """
    Auto-fix scalar method calls in custom_exit/custom_stoploss.
    Returns (fixed_code, list_of_fixes_applied).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, []

    fixer = _ScalarMethodFixer()
    new_tree = fixer.visit(tree)
    if not fixer.fixes:
        return source_code, []

    ast.fix_missing_locations(new_tree)
    fixed_code = ast.unparse(new_tree)
    return fixed_code, fixer.fixes


def _fix_imports(source_code: str) -> tuple[str, list[str]]:
    """
    Ensure all Parameter types used in code are imported from freqtrade.strategy.
    Returns (fixed_code, list_of_fixes_applied).
    """
    PARAM_TYPES = {"IntParameter", "DecimalParameter", "CategoricalParameter", "BooleanParameter"}

    used = {p for p in PARAM_TYPES if re.search(rf"\b{p}\b", source_code)}
    if not used:
        return source_code, []

    fixes: list[str] = []

    # Try multi-line import first: from freqtrade.strategy import (...)
    multi_pattern = re.compile(r"from\s+freqtrade\.strategy\s+import\s+\((.*?)\)", re.DOTALL)
    m = multi_pattern.search(source_code)
    if m:
        imported_block = m.group(1)
        missing = [p for p in used if p not in imported_block]
        if missing:
            new_block = imported_block.rstrip().rstrip(",") + ",\n    " + ",\n    ".join(missing)
            source_code = source_code[: m.start(1)] + new_block + source_code[m.end(1) :]
            fixes.extend(missing)
        return source_code, fixes

    # Try single-line import: from freqtrade.strategy import X, Y
    single_pattern = re.compile(r"from\s+freqtrade\.strategy\s+import\s+(.+?)(?:\n|$)")
    m = single_pattern.search(source_code)
    if m:
        imported = m.group(1)
        missing = [p for p in used if p not in imported]
        if missing:
            new_import = imported.rstrip() + ", " + ", ".join(missing)
            source_code = source_code[: m.start(1)] + new_import + source_code[m.end(1) :]
            fixes.extend(missing)
        return source_code, fixes

    # No freqtrade.strategy import found - add one after the last import line
    import_line = "from freqtrade.strategy import (\n    " + ",\n    ".join(sorted(used)) + ",\n)\n"
    last_import_match = None
    for mm in re.finditer(r"^(?:from\s+\S+\s+import|import\s+\S+)", source_code, re.MULTILINE):
        last_import_match = mm
    if last_import_match:
        pos = last_import_match.end()
        source_code = source_code[:pos] + "\n" + import_line + source_code[pos:]
    else:
        source_code = import_line + "\n" + source_code
    fixes.extend(used)
    return source_code, fixes


def _fix_is_long(source_code: str) -> tuple[str, list[str]]:
    """
    Replace trade.is_long with not trade.is_short (freqtrade LocalTrade has no is_long).
    Handles both plain and negated forms.
    Returns (fixed_code, list_of_fixes_applied).
    """
    fixes: list[str] = []
    # not trade.is_long -> trade.is_short
    new_code, count1 = re.subn(r"\bnot\s+trade\.is_long\b", "trade.is_short", source_code)
    if count1:
        fixes.append(f"not trade.is_long -> trade.is_short ({count1}x)")
    # trade.is_long -> not trade.is_short
    new_code, count2 = re.subn(r"\btrade\.is_long\b", "not trade.is_short", new_code)
    if count2:
        fixes.append(f"trade.is_long -> not trade.is_short ({count2}x)")
    return new_code, fixes


def _fix_entry_tag(source_code: str) -> tuple[str, list[str]]:
    """
    Replace trade.entry_tag with trade.enter_tag (freqtrade renamed the attribute).
    Returns (fixed_code, list_of_fixes_applied).
    """
    fixes: list[str] = []
    new_code, count = re.subn(r"\btrade\.entry_tag\b", "trade.enter_tag", source_code)
    if count:
        fixes.append(f"trade.entry_tag -> trade.enter_tag ({count}x)")
    return new_code, fixes


def _fix_nbdevup_type(source_code: str) -> tuple[str, list[str]]:
    """
    Fix nbdevup/nbdevdn parameter types: IntParameter -> DecimalParameter.
    Also fix direct integer literals passed to ta-lib BBANDS (must be float).
    Returns (fixed_code, list_of_fixes_applied).
    """
    fixes: list[str] = []
    # Fix IntParameter for nbdevup/nbdevdn -> DecimalParameter
    new_code, count = re.subn(
        r"(\bnbdevup\s*=\s*)IntParameter",
        r"\1DecimalParameter",
        source_code,
    )
    if count:
        fixes.append(f"nbdevup IntParameter -> DecimalParameter ({count}x)")

    new_code, count = re.subn(
        r"(\bnbdevdn\s*=\s*)IntParameter",
        r"\1DecimalParameter",
        new_code,
    )
    if count:
        fixes.append(f"nbdevdn IntParameter -> DecimalParameter ({count}x)")

    # Fix direct integer literals in ta-lib calls, e.g. nbdevup=1 -> nbdevup=1.0
    new_code, count = re.subn(
        r"(\bnbdevup\s*=\s*)(\d+)(?!\.|\d)",
        r"\1\2.0",
        new_code,
    )
    if count:
        fixes.append(f"nbdevup int literal -> float ({count}x)")

    new_code, count = re.subn(
        r"(\bnbdevdn\s*=\s*)(\d+)(?!\.|\d)",
        r"\1\2.0",
        new_code,
    )
    if count:
        fixes.append(f"nbdevdn int literal -> float ({count}x)")

    return new_code, fixes


def _fix_column_names(source_code: str) -> tuple[str, list[str]]:
    """
    Fix column name case mismatches between populate_indicators and other methods.
    If populate_indicators defines dataframe["adx"] but entry/exit uses dataframe["ADX"],
    auto-correct to the defined case.
    Returns (fixed_code, list_of_fixes_applied).
    """
    fixes: list[str] = []

    # Extract column names defined in populate_indicators (assignment targets)
    defined: dict[str, str] = {}  # lowercase -> original case
    def_pattern = re.compile(r'dataframe\[(?:\'|\"|\`)([^\'"`\]]+)(?:\'|\"|\`)\]\s*=')
    for m in def_pattern.finditer(source_code):
        col_name = m.group(1)
        lower = col_name.lower()
        if lower not in defined:
            defined[lower] = col_name

    if not defined:
        return source_code, fixes

    # Fix references everywhere to use the defined case
    ref_pattern = re.compile(r'dataframe\[(\'|\"|\`)([^\'"`\]]+)(\1)\]')

    def _replacer(m: re.Match) -> str:
        quote = m.group(1)
        col_name = m.group(2)
        lower = col_name.lower()
        if lower in defined and defined[lower] != col_name:
            fixes.append(f"dataframe['{col_name}'] -> dataframe['{defined[lower]}']")
            return f"dataframe[{quote}{defined[lower]}{quote}]"
        return m.group(0)

    fixed_code = ref_pattern.sub(_replacer, source_code)
    return fixed_code, fixes


def _extract_code_skeleton(source_code: str) -> str:
    """Extract strategy skeleton: imports + class attrs + populate_indicators body + method signatures."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code

    lines = source_code.split("\n")
    output_lines: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            output_lines.append(lines[node.lineno - 1])
            continue

        if isinstance(node, ast.ClassDef):
            output_lines.append(lines[node.lineno - 1])

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == "populate_indicators":
                        end = item.end_lineno or item.lineno
                        output_lines.extend(lines[item.lineno - 1 : end])
                    else:
                        output_lines.append(lines[item.lineno - 1])
                        indent = item.body[0].col_offset if item.body else 4
                        output_lines.append(
                            " " * indent + "# [body preserved from previous version]"
                        )
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    end = item.end_lineno or item.lineno
                    output_lines.extend(lines[item.lineno - 1 : end])
            break

    return "\n".join(output_lines)


class Mutator:
    def __init__(self, config: EvolutionConfig):
        self.config = config
        llm = config.llm
        self.llm_config = llm
        self.program_md = _load_program_md()
        self._iteration = 0
        self._base_name = config.strategy_name

    def propose_mutation(  # noqa: C901
        self,
        source_code: str,
        baseline_is: BacktestMetrics,
        best_snapshot: StrategySnapshot | None,
        history: list[ExperimentRecord],
    ) -> tuple[MutationRecord, str]:
        """Ask LLM to propose a mutation. Returns (record, mutated_code)."""
        # Track parent for params file copying
        if best_snapshot:
            self._parent_strategy = best_snapshot.strategy_name

        # Load blacklist rules for injection into prompt
        from blacklist import load_blacklist
        blacklist_rules = load_blacklist(self.config)

        prompt = _build_prompt(
            self.config, source_code, baseline_is, best_snapshot, history,
            blacklist_rules=blacklist_rules,
        )

        providers = self.llm_config.ordered_providers()
        provider_names = [provider.name for provider in providers]
        max_output_tokens = self.llm_config.max_tokens
        logger.info(
            f"Prompt length: {len(prompt)} chars, providers={provider_names}, "
            f"max_tokens={max_output_tokens}"
        )

        # Token budget check: trim prompt if it exceeds provider context window
        for provider in providers:
            budget = (
                provider.effective_max_context_tokens(self.llm_config)
                - (max_output_tokens or 32768)
                - 2048
            )
            total_estimated = estimate_tokens(self.program_md + prompt)
            if total_estimated <= budget:
                break
            # Tiered trimming
            for tier, trim_kwargs in enumerate(
                [
                    {"max_history": 10, "max_failures": 8},
                    {"max_history": 5, "max_failures": 4},
                    {"max_history": 3, "max_failures": 2},
                    {"max_history": 3, "max_failures": 2},
                ],
                start=1,
            ):
                rebuilt = _build_prompt(
                    self.config, source_code, baseline_is, best_snapshot, history,
                    blacklist_rules=blacklist_rules,
                    **trim_kwargs,
                )
                if tier == 4:
                    rebuilt = rebuilt.replace(
                        source_code,
                        _extract_code_skeleton(source_code),
                    )
                if estimate_tokens(self.program_md + rebuilt) <= budget:
                    logger.info(
                        f"Prompt trimmed to tier {tier} for provider {provider.name}: "
                        f"{len(prompt)} -> {len(rebuilt)} chars"
                    )
                    prompt = rebuilt
                    break
            else:
                prompt = rebuilt
            break

        def _call_llm(provider: LLMProviderConfig, api_key: str) -> tuple[str, str]:
            import httpx

            request_timeout = provider.effective_request_timeout(self.llm_config)
            url = f"{provider.base_url.rstrip('/')}/v1/messages"
            payload = {
                "model": provider.model,
                "system": self.program_md,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": provider.effective_temperature(self.llm_config),
                "max_tokens": provider.effective_max_tokens(self.llm_config),
            }
            payload.update(provider.extra_body)
            logger.info(
                f"LLM request starting provider={provider.name} model={provider.model}"
            )
            with httpx.Client(timeout=httpx.Timeout(request_timeout)) as http_client:
                response = http_client.post(
                    url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                if response.status_code == 400:
                    error_text = response.text.lower()
                    if any(
                        kw in error_text
                        for kw in ("token limit", "context window", "exceeded")
                    ):
                        raise TokenLimitError(
                            provider.name, provider.model,
                            requested=0, limit=0,
                        )
                response.raise_for_status()
                data = response.json()
            text = "".join(
                part.get("text", "")
                for part in data.get("content", [])
                if isinstance(part, dict) and part.get("type") == "text"
            )
            return text, data.get("stop_reason", "")

        # Retry with exponential backoff, then fall back across configured providers.
        last_exception: Exception | None = None
        llm_result: tuple[str, str] | None = None
        used_provider: LLMProviderConfig | None = None
        if not providers:
            raise ValueError("No enabled LLM providers configured")

        for provider in providers:
            try:
                api_key = provider.get_api_key()
            except ValueError as e:
                last_exception = e
                logger.warning(f"Skipping LLM provider {provider.name}: {e}")
                continue

            max_retries = provider.effective_max_retries(self.llm_config)
            request_timeout = provider.effective_request_timeout(self.llm_config)
            for attempt in range(1, max_retries + 1):
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_call_llm, provider, api_key)
                        llm_result = future.result(timeout=request_timeout + 5)
                    used_provider = provider
                    break
                except concurrent.futures.TimeoutError:
                    last_exception = TimeoutError(
                        f"LLM provider {provider.name} timed out after "
                        f"{request_timeout} seconds"
                    )
                    logger.warning(
                        f"LLM request timed out provider={provider.name} "
                        f"(attempt {attempt}/{max_retries})"
                    )
                    if attempt < max_retries:
                        import time
                        time.sleep(2 ** attempt)
                except TokenLimitError as e:
                    last_exception = e
                    logger.warning(
                        f"Token limit exceeded provider={provider.name}: {e}"
                    )
                    break  # Move to next provider
                except Exception as e:
                    last_exception = e
                    logger.warning(
                        f"LLM request failed provider={provider.name} "
                        f"(attempt {attempt}/{max_retries}): {type(e).__name__}: {e}"
                    )
                    if attempt < max_retries:
                        import time
                        time.sleep(2 ** attempt)
            if llm_result is not None:
                break
        else:
            raise last_exception or RuntimeError("LLM request failed after retries")

        text, finish_reason = llm_result
        logger.info(
            f"LLM provider={used_provider.name if used_provider else 'unknown'}, "
            f"finish_reason={finish_reason}, content_len={len(text)}, "
            f"content_is_none={not bool(text)}"
        )

        analysis, code, impact = _parse_response(text)
        if not code:
            logger.error(f"LLM response parsing failed. Raw response:\n{text[:2000]}")
            raise ValueError("LLM did not return valid code")

        # Auto-fix scalar method calls that LLM keeps generating despite warnings
        fixed_code, fixes = _fix_scalar_methods(code)
        if fixes:
            logger.warning(
                f"Auto-fixed scalar method calls in LLM output: {set(fixes)}. "
                "These calls were removed from custom_exit/custom_stoploss."
            )
            code = fixed_code

        # Auto-fix missing imports for Parameter types
        fixed_code, import_fixes = _fix_imports(code)
        if import_fixes:
            logger.warning(
                f"Auto-fixed missing imports in LLM output: {set(import_fixes)}."
            )
            code = fixed_code

        # Auto-fix trade.is_long -> not trade.is_short
        fixed_code, is_long_fixes = _fix_is_long(code)
        if is_long_fixes:
            logger.warning(
                f"Auto-fixed is_long references in LLM output: {is_long_fixes}."
            )
            code = fixed_code

        # Auto-fix trade.entry_tag -> trade.enter_tag
        fixed_code, entry_tag_fixes = _fix_entry_tag(code)
        if entry_tag_fixes:
            logger.warning(
                f"Auto-fixed entry_tag references in LLM output: {entry_tag_fixes}."
            )
            code = fixed_code

        # Auto-fix column name case mismatches
        fixed_code, col_fixes = _fix_column_names(code)
        if col_fixes:
            logger.warning(
                f"Auto-fixed column name case mismatches in LLM output: {col_fixes}."
            )
            code = fixed_code

        # Auto-fix nbdevup/nbdevdn parameter types
        fixed_code, nbdev_fixes = _fix_nbdevup_type(code)
        if nbdev_fixes:
            logger.warning(
                f"Auto-fixed nbdevup/nbdevdn parameter types in LLM output: {nbdev_fixes}."
            )
            code = fixed_code

        self._iteration += 1
        mutation = MutationRecord(
            mutation_type="llm_driven",
            description=f"Iter {self._iteration}: {analysis[:200]}",
            llm_analysis=analysis,
            llm_expected_impact=impact,
        )
        return mutation, code

    def apply_mutation(
        self, source_code: str, iteration: int, output_dir: str
    ) -> tuple[str, Path]:
        """Write mutated strategy to disk. Returns (name, path)."""
        file_name = f"{self._base_name}_E{iteration:04d}"
        file_path = Path(output_dir) / f"{file_name}.py"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Rename class to match filename
        modified = re.sub(r"class\s+\w+", f"class {file_name}", source_code, count=1)
        file_path.write_text(modified)

        # Copy hyperopt params file if it exists for the parent strategy
        # so freqtrade loads optimized parameters instead of defaults
        self._copy_params_file(file_name, output_dir)

        logger.info(f"Written: {file_path}")
        return file_name, file_path

    def _copy_params_file(self, new_strategy_name: str, output_dir: str) -> None:
        """Copy the best strategy's params JSON so the new strategy loads it."""
        if not self._parent_strategy:
            new_json = Path(output_dir) / f"{new_strategy_name}.json"
            if new_json.exists():
                new_json.unlink()
            return
        parent_json = Path(output_dir) / f"{self._parent_strategy}.json"
        # Fallback to parent directory when output_dir is strategies/autoresearch/.
        if not parent_json.exists():
            parent_json = Path(output_dir).parent / f"{self._parent_strategy}.json"
        new_json = Path(output_dir) / f"{new_strategy_name}.json"
        if parent_json.exists():
            import json
            data = json.loads(parent_json.read_text())
            data["strategy_name"] = new_strategy_name
            new_json.write_text(json.dumps(data, indent=2))
        elif new_json.exists():
            new_json.unlink()

    _parent_strategy: str = ""

    @staticmethod
    def _extract_method_ast(source: str, method_name: str) -> ast.AST | None:
        """Extract a method's AST node from strategy source code."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == method_name
                    ):
                        return item
        return None

    @staticmethod
    def _methods_equal(a: ast.AST | None, b: ast.AST | None) -> bool:
        """Compare two AST nodes for structural equality."""
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return ast.dump(a) == ast.dump(b)

    @staticmethod
    def _extract_class_attr_ast(source: str, attr_name: str) -> ast.AST | None:
        """Extract a class-level attribute assignment AST node."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name) and target.id == attr_name:
                                return item.value
                    elif (
                        isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)
                        and item.target.id == attr_name
                    ):
                        return item.value
        return None

    @staticmethod
    def _parameter_space(param_name: str, call: ast.Call) -> str | None:
        """Infer a freqtrade hyperopt space from a Parameter call."""
        for keyword in call.keywords:
            if keyword.arg == "space" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                if isinstance(value, str):
                    if value == "enter":
                        return "buy"
                    if value == "exit":
                        return "sell"
                    return value

        if param_name.startswith(("buy_", "enter_")):
            return "buy"
        if param_name.startswith(("sell_", "exit_")):
            return "sell"
        if param_name.startswith("protection_"):
            return "protection"
        return None

    @staticmethod
    def _extract_hyperopt_params_by_space(  # noqa: C901
        source: str,
    ) -> dict[str, dict[str, str]]:
        """Extract hyperopt Parameter definitions grouped by space."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return {}

        params: dict[str, dict[str, str]] = {}
        parameter_types = {
            "IntParameter",
            "DecimalParameter",
            "RealParameter",
            "CategoricalParameter",
            "BooleanParameter",
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                value: ast.AST | None = None
                target_name: str | None = None
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                    value = item.value
                    if len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                        target_name = item.targets[0].id
                elif isinstance(item, ast.AnnAssign) and isinstance(item.value, ast.Call):
                    value = item.value
                    if isinstance(item.target, ast.Name):
                        target_name = item.target.id

                if target_name is None or not isinstance(value, ast.Call):
                    continue

                func = value.func
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                else:
                    continue

                if func_name not in parameter_types:
                    continue

                space = Mutator._parameter_space(target_name, value)
                if space is None:
                    continue
                params.setdefault(space, {})[target_name] = ast.dump(value)

        return params

    @staticmethod
    def detect_changed_spaces(original_code: str, mutated_code: str) -> list[str]:
        """
        Compare AST of entry/exit methods between original and mutated code.
        Returns list of spaces that should be included in hyperopt.
        If a method did not change, its corresponding space can be skipped
        to save optimization time.
        """
        changed: list[str] = []

        entry_unchanged = Mutator._methods_equal(
            Mutator._extract_method_ast(original_code, "populate_entry_trend"),
            Mutator._extract_method_ast(mutated_code, "populate_entry_trend"),
        )
        if not entry_unchanged:
            changed.append("buy")

        exit_unchanged = Mutator._methods_equal(
            Mutator._extract_method_ast(original_code, "populate_exit_trend"),
            Mutator._extract_method_ast(mutated_code, "populate_exit_trend"),
        )
        if not exit_unchanged:
            changed.append("sell")

        indicator_unchanged = Mutator._methods_equal(
            Mutator._extract_method_ast(original_code, "populate_indicators"),
            Mutator._extract_method_ast(mutated_code, "populate_indicators"),
        )
        if not indicator_unchanged:
            changed.extend(["buy", "sell"])

        custom_exit_unchanged = Mutator._methods_equal(
            Mutator._extract_method_ast(original_code, "custom_exit"),
            Mutator._extract_method_ast(mutated_code, "custom_exit"),
        )
        if not custom_exit_unchanged:
            changed.append("sell")

        # custom_stoploss changes imply stoploss and sell spaces should be optimized.
        stoploss_unchanged = Mutator._methods_equal(
            Mutator._extract_method_ast(original_code, "custom_stoploss"),
            Mutator._extract_method_ast(mutated_code, "custom_stoploss"),
        )
        if not stoploss_unchanged:
            changed.extend(["sell", "stoploss"])

        stoploss_attr_unchanged = Mutator._methods_equal(
            Mutator._extract_class_attr_ast(original_code, "stoploss"),
            Mutator._extract_class_attr_ast(mutated_code, "stoploss"),
        )
        if not stoploss_attr_unchanged:
            changed.append("stoploss")

        original_params = Mutator._extract_hyperopt_params_by_space(original_code)
        mutated_params = Mutator._extract_hyperopt_params_by_space(mutated_code)
        for space in sorted(set(original_params) | set(mutated_params)):
            normalized_space = "buy" if space == "enter" else "sell" if space == "exit" else space
            if original_params.get(space, {}) != mutated_params.get(space, {}):
                changed.append(normalized_space)

        return sorted(set(changed))

    def cleanup_strategy(self, file_path: Path) -> None:
        """Remove a rejected strategy file and its params/cache artifacts."""
        if file_path.exists():
            file_path.unlink()
        params_path = file_path.with_suffix(".json")
        if params_path.exists():
            params_path.unlink()
        pycache_dir = file_path.parent / "__pycache__"
        if pycache_dir.exists():
            for cached_file in pycache_dir.glob(f"{file_path.stem}.*.pyc"):
                cached_file.unlink()
