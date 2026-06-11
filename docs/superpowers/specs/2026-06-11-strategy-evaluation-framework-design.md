# Strategy Evaluation Framework Design

**Date:** 2026-06-11
**Status:** Approved
**Context:** After optimizing EfutureLong from v1 (45% DD) to v2 (5.3% DD), we discussed what makes a good crypto quant strategy.

## Core Philosophy

A good strategy = earning returns stably and repeatably within acceptable risk, surviving all market conditions.

User preferences:
- Balanced evaluation (not single-metric driven)
- Max drawdown tolerance: 10-20%
- Trading frequency required for stability (not too few trades)
- OOS must be profitable and show no overfitting
- Goals: stable compound growth + market adaptability

## Three-Layer Evaluation System

### Layer 1: Hard Gates (veto if any fails)

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Profit Factor | > 1.2 | Below 1.2 = unreliable profit |
| Max Drawdown | < 20% | User's acceptable upper limit |
| OOS Profitable | PF > 1.0 | Must make money out of sample |
| Min Trade Count | >= 100 per training period | Insufficient sample to validate |
| Max Consecutive Losses | < 8 | Psychological tolerance limit |

### Layer 2: Composite Score (0-100)

| Dimension | Weight | Metrics | Target |
|-----------|--------|---------|--------|
| Risk Control | 30% | Max DD + Calmar | DD <15%, Calmar >3 |
| Risk-Adj Return | 25% | Sortino + PF | Sortino >1.5, PF >1.5 |
| Stability | 25% | Rolling window consistency | All windows profitable, CV <0.25 |
| Sustainability | 20% | OOS degradation + trade frequency | OOS >= 50% training, >= 0.5 trades/day |

### Layer 3: Anti-Overfit Validation

- OOS Sortino >= 50% of training Sortino
- Rolling window CV < 0.30
- Parameter sensitivity: +/- 10% change causes < 30% performance drop
- All 6+ rolling windows profitable

## Key Differences from Previous Skill

1. Sharpe -> Sortino as primary (crypto has fat tails)
2. Max DD threshold: 10% -> 20% (more realistic for crypto)
3. Added minimum trade frequency requirement
4. Added three-layer evaluation (gate -> score -> validate)
5. Added market adaptability checks (bull/bear/range/high-vol/low-vol)
