"""
AutoResearch for Freqtrade - Blacklist Lesson System

Per-strategy and global blacklists that track failed mutation patterns
and inject them as hard constraints into LLM mutation prompts.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import EvolutionConfig
from tracker import ExperimentRecord


logger = logging.getLogger(__name__)


@dataclass
class BlacklistRule:
    id: str
    pattern: str
    description: str
    reason: str = ""
    created_at: str = ""
    failed_count: int = 0
    source_experiments: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _next_rule_id(existing_rules: list[BlacklistRule]) -> str:
    max_num = 0
    for rule in existing_rules:
        match = re.match(r"BL(\d+)", rule.id)
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"BL{max_num + 1:03d}"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"rules": []}
    with path.open() as f:
        data = json.load(f)
    return data


def _save_json(path: Path, data: dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    with path.open("w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_rules(data: dict[str, Any]) -> list[BlacklistRule]:
    rules: list[BlacklistRule] = []
    for r in data.get("rules", []):
        source_exp = r.get("source_experiments", ())
        if isinstance(source_exp, list):
            source_exp = tuple(source_exp)
        rules.append(BlacklistRule(
            id=r["id"],
            pattern=r["pattern"],
            description=r["description"],
            reason=r.get("reason", ""),
            created_at=r.get("created_at", ""),
            failed_count=r.get("failed_count", 0),
            source_experiments=source_exp,
        ))
    return rules


def load_blacklist(config: EvolutionConfig) -> list[BlacklistRule]:
    """Load strategy-specific blacklist merged with global rules."""
    rules: list[BlacklistRule] = []

    # Global rules first
    global_file = config.global_blacklist_file
    if global_file.exists():
        data = _load_json(global_file)
        rules.extend(_parse_rules(data))

    # Strategy-specific rules (override global if same id)
    strat_file = config.strategy_blacklist_file
    if strat_file.exists():
        data = _load_json(strat_file)
        strat_rules = _parse_rules(data)
        existing_ids = {r.id for r in rules}
        for r in strat_rules:
            if r.id not in existing_ids:
                rules.append(r)
            else:
                # Strategy-specific overrides global
                rules = [r if existing.id == r.id else existing for existing in rules]

    return rules


def save_blacklist(config: EvolutionConfig, rules: list[BlacklistRule]) -> None:
    """Save strategy-specific blacklist."""
    strat_file = config.strategy_blacklist_file
    _save_json(strat_file, {"rules": [r.to_dict() for r in rules]})


def add_rule(config: EvolutionConfig, rule: BlacklistRule) -> None:
    """Add a rule to the strategy-specific blacklist."""
    strat_file = config.strategy_blacklist_file
    strat_data = _load_json(strat_file)
    strat_rules = _parse_rules(strat_data)
    existing_ids = {r.id for r in strat_rules}
    if rule.id in existing_ids:
        logger.warning(f"Rule {rule.id} already exists, skipping")
        return
    strat_rules.append(rule)
    _save_json(strat_file, {"rules": [r.to_dict() for r in strat_rules]})
    logger.info(f"Added blacklist rule {rule.id}: {rule.description}")


def remove_rule(config: EvolutionConfig, rule_id: str) -> bool:
    """Remove a rule from the strategy-specific blacklist.

    Returns True if the rule was found and removed.
    """
    strat_file = config.strategy_blacklist_file
    strat_data = _load_json(strat_file)
    strat_rules = _parse_rules(strat_data)
    original_count = len(strat_rules)
    strat_rules = [r for r in strat_rules if r.id != rule_id]
    if len(strat_rules) == original_count:
        return False
    _save_json(strat_file, {"rules": [r.to_dict() for r in strat_rules]})
    logger.info(f"Removed blacklist rule {rule_id}")
    return True


def detect_failure_pattern(
    recent_experiments: list[ExperimentRecord],
) -> BlacklistRule | None:
    """Analyze recent failed experiments to detect common failure patterns.

    Looks for patterns in rejection reasons and mutation descriptions
    across consecutive failed experiments.
    """
    if len(recent_experiments) < 3:
        return None

    failures = [
        ex for ex in recent_experiments
        if ex.status in ("rejected", "regression", "error")
    ]
    if len(failures) < 3:
        return None

    patterns: dict[str, list[ExperimentRecord]] = {}
    for ex in failures:
        reason = ex.rejection_reason or ""
        desc = ex.mutation.description.lower()

        if any(kw in desc for kw in ("trend filter", "trend_filter", "ema filter", "sma filter")):
            patterns.setdefault("adding_trend_filter", []).append(ex)
        if any(kw in desc for kw in ("volume multiplier", "volume filter", "volume_mult")):
            patterns.setdefault("adding_volume_filter", []).append(ex)
        if "oos trades" in reason.lower() or "oos trade" in reason.lower():
            patterns.setdefault("oos_trade_collapse", []).append(ex)
        if "overfitting" in reason.lower() or "sharpe" in reason.lower():
            patterns.setdefault("overfitting", []).append(ex)

    for pattern_name, experiments in patterns.items():
        if len(experiments) >= 3:
            descriptions = {
                "adding_trend_filter": "Adding EMA/SMA trend filter as entry gate",
                "adding_volume_filter": "Using volume multiplier as entry condition",
                "oos_trade_collapse": "Changes that collapse OOS trade count",
                "overfitting": "Changes causing severe IS/OOS performance gap",
            }
            return BlacklistRule(
                id="",  # Assigned on save
                pattern=pattern_name,
                description=descriptions.get(pattern_name, pattern_name),
                reason=(
                    f"Detected from {len(experiments)} consecutive failures: "
                    + "; ".join(ex.rejection_reason or "unknown" for ex in experiments[:5])
                ),
                created_at=_now_iso(),
                failed_count=len(experiments),
                source_experiments=tuple(ex.experiment_id for ex in experiments[:10]),
            )

    return None


def inject_into_prompt(prompt: str, rules: list[BlacklistRule]) -> str:
    """Inject blacklist rules as hard constraints at the top of mutation prompt."""
    if not rules:
        return prompt

    lines = [
        "[BLACKLIST - MUST FOLLOW]",
    ]
    for rule in rules:
        lines.append(f"- {rule.id}: {rule.description}")
        if rule.reason:
            lines.append(f"  Reason: {rule.reason[:200]}")
    lines.append(
        f"[Above rules from {sum(r.failed_count for r in rules)}+ failed experiments. "
        "Violations will be rejected immediately]"
    )
    lines.append("")

    return "\n".join(lines) + prompt


def check_global_promotion(
    config: EvolutionConfig,
    blacklist_dir: Path,
) -> list[BlacklistRule]:
    """Find rules that appear in 3+ strategy blacklists.

    Returns candidates for promotion to global blacklist.
    User confirmation is required before actual promotion.
    """
    if not blacklist_dir.exists():
        return []

    pattern_occurrences: dict[str, list[tuple[str, BlacklistRule]]] = {}
    for json_file in sorted(blacklist_dir.glob("*.json")):
        if json_file.name == "global.json":
            continue
        strategy_name = json_file.stem
        data = _load_json(json_file)
        for rule in _parse_rules(data):
            pattern_occurrences.setdefault(rule.pattern, []).append(
                (strategy_name, rule)
            )

    threshold = config.global_blacklist_threshold
    candidates: list[BlacklistRule] = []
    for pattern, occurrences in pattern_occurrences.items():
        if len(occurrences) >= threshold:
            best_rule = max(occurrences, key=lambda x: len(x[1].reason))[1]
            total_failures = sum(r.failed_count for _, r in occurrences)
            strategies = [s for s, _ in occurrences]
            candidates.append(BlacklistRule(
                id="",
                pattern=pattern,
                description=best_rule.description,
                reason=(
                    f"Appears in {len(occurrences)} strategies: "
                    f"{', '.join(strategies)}. "
                    f"Total failures: {total_failures}. "
                    f"{best_rule.reason}"
                ),
                created_at=_now_iso(),
                failed_count=total_failures,
                source_experiments=tuple(
                    eid for _, r in occurrences for eid in r.source_experiments
                ),
            ))

    return candidates


def propose_global_rule(rule: BlacklistRule, blacklist_dir: Path) -> None:
    """Promote a rule to global blacklist after user confirmation."""
    global_file = blacklist_dir / "global.json"
    data = _load_json(global_file)
    rules = _parse_rules(data)

    rule_id = _next_rule_id(rules)
    updated_rule = BlacklistRule(
        id=rule_id,
        pattern=rule.pattern,
        description=rule.description,
        reason=rule.reason,
        created_at=rule.created_at or _now_iso(),
        failed_count=rule.failed_count,
        source_experiments=rule.source_experiments,
    )
    rules.append(updated_rule)
    _save_json(global_file, {"rules": [r.to_dict() for r in rules]})
    logger.info(f"Promoted rule to global blacklist: {rule_id}")
