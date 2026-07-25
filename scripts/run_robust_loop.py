"""Robust cross-window loop optimization for EfutureLongKelvin.

Like run_efuture_loop but the screening uses MULTIPLE holdout windows and a
robustness score, to avoid the single-OOS-window overfit that v3-v11 suffered.

Flow: hyperopt (train window) -> top-K by train backtest-sortino -> each tested
on N holdout windows -> robust score -> save only if all holdouts profitable
AND robust score beats v1's robust score.

Usage: python run_robust_loop.py <iteration> <seed> [--reuse]
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ITER = int(sys.argv[1])
SEED = int(sys.argv[2])
REUSE = "--reuse" in sys.argv

ROOT = Path("/Users/kelvin/projects/freqtrade")
STATE = ROOT / "user_data/strategies/efuture-long-kelvin/_loop_state.json"
CONFIG = "user_data/configs/dryrun/EfutureIter.json"
SEARCH = "EfutureLongKelvin_search"
SEARCH_PY = ROOT / f"user_data/strategies/efuture-long-kelvin/{SEARCH}.py"
SEARCH_JSON = ROOT / f"user_data/strategies/efuture-long-kelvin/{SEARCH}.json"
LONG_DIR = ROOT / "user_data/strategies/efuture-long-kelvin"
TRAIN = "20240801-20251130"  # leave recent 6 months as holdout
HOLDOUTS = [
    ("20251201", "20260131"),
    ("20260201", "20260331"),
    ("20260401", "20260531"),
    ("20260601", "20260611"),
]
EPOCHS = 400
LOSS = "SortinoHyperOptLossDaily"
SPACES = ["buy", "sell"]
TOP_K = 15
V1 = "EfutureLongKelvin_v1"  # robustness baseline

LOG = LONG_DIR / f"robust_iter_{ITER}.log"


def log(msg):
    line = f"[robust {ITER}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def _grab(stdout, pat):
    m = re.search(pat, stdout)
    return m.group(1) if m else None


def parse_metrics(stdout):
    profit = _grab(stdout, r"Total profit %\s*[│|]\s*([\-\d.]+)%")
    sortino = _grab(stdout, r"Sortino\s*[│|]\s*([\-\d.]+)")
    pf = _grab(stdout, r"Profit factor\s*[│|]\s*([\-\d.]+)")
    dd = _grab(stdout, r"Absolute drawdown\s*[│|].*?\(([\d.]+)%\)")
    trades = _grab(stdout, r"Total/Daily Avg Trades\s*[│|]\s*(\d+)")
    return {
        "profit_pct": float(profit) if profit else None,
        "sortino": float(sortino) if sortino else None,
        "profit_factor": float(pf) if pf else None,
        "dd_pct": float(dd) / 100 if dd else None,
        "trades": int(trades) if trades else None,
    }


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def backtest_window(strategy, tr_start, tr_end):
    cmd = [".venv/bin/freqtrade", "backtesting", "--strategy", strategy, "--config",
           CONFIG, "--timerange", f"{tr_start}-{tr_end}", "--fee", "0.0005",
           "--enable-protections"]
    for attempt in range(6):
        p = run(cmd)
        m = parse_metrics(p.stdout)
        if m["profit_pct"] is not None:
            return m
        time.sleep(10)
    return m


def robust_score(metrics_list):
    """metrics_list: per-holdout metrics. Higher is better. Returns (score, pass_gate)."""
    sortinos = [m["sortino"] for m in metrics_list if m["sortino"] is not None]
    profits = [m["profit_pct"] for m in metrics_list if m["profit_pct"] is not None]
    pfs = [m["profit_factor"] for m in metrics_list if m["profit_factor"] is not None]
    dds = [m["dd_pct"] for m in metrics_list if m["dd_pct"] is not None]
    if len(sortinos) < len(HOLDOUTS) or len(profits) < len(HOLDOUTS):
        return None, False
    mean_sort = sum(sortinos) / len(sortinos)
    mean_prof = sum(profits) / len(profits)
    max_dd = max(dds)
    mean_prof_safe = abs(mean_prof) if abs(mean_prof) > 1e-6 else 1e-6
    std_prof = (sum((p - mean_prof) ** 2 for p in profits) / len(profits)) ** 0.5
    cv_prof = std_prof / mean_prof_safe
    score = mean_sort * 8 + mean_prof - max_dd * 30 - cv_prof * 15
    pass_gate = (all(p > 1.0 for p in pfs) and all(pr > 0 for pr in profits)
                 and max_dd < 0.25 and all(m["trades"] and m["trades"] >= 5 for m in metrics_list))
    return score, pass_gate


def find_latest_search_fthypt():
    files = sorted((ROOT / "user_data/hyperopt_results").glob(
        "strategy_EfutureLongKelvin_search_*.fthypt"), key=lambda f: f.stat().st_mtime)
    return files[-1].name if files else None


def load_top_k(fthypt_name, k=TOP_K):
    f = ROOT / f"user_data/hyperopt_results/{fthypt_name}"
    lines = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    valid = [l for l in lines
             if l.get("loss", 100) < 99 and l.get("results_metrics", {}).get("total_trades", 0) > 0]
    valid.sort(key=lambda x: -x["results_metrics"].get("sortino", -999.0))
    return [(l["params_dict"], l["results_metrics"]) for l in valid[:k]]


def params_to_ft(pd, name):
    buy_p = {k: v for k, v in pd.items() if k.startswith("buy_")}
    sell_p = {k: v for k, v in pd.items()
              if not k.startswith("buy_") and not k.startswith("roi_") and k != "stoploss"}
    return {
        "strategy_name": name,
        "params": {
            "trailing": {"trailing_stop": False, "trailing_stop_positive": None,
                         "trailing_stop_positive_offset": 0.0,
                         "trailing_only_offset_is_reached": False},
            "max_open_trades": {"max_open_trades": 3},
            "buy": buy_p, "sell": sell_p,
            "stoploss": {"stoploss": pd.get("stoploss", -0.287)},
        },
        "ft_stratparam_v": 1,
    }


def next_version():
    n = 11  # v3-v11 exist
    for d in [ROOT / "user_data/strategies", LONG_DIR]:
        for f in d.glob("EfutureLongKelvin_v*.py"):
            m = re.search(r"_v(\d+)\.py$", f.name)
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


def main():
    LOG.unlink(missing_ok=True)
    log(f"=== robust iteration {ITER} seed {SEED} reuse={REUSE} ===")

    # baseline robust score of v1 on the 4 holdouts (computed once, cached in state)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    base_robust = state.get("robust_baseline_score")
    if base_robust is None:
        v1_metrics = []
        for bl_attempt in range(3):
            log(f"computing v1 robust baseline on 4 holdouts (attempt {bl_attempt+1})...")
            v1_metrics = [backtest_window(V1, s, e) for s, e in HOLDOUTS]
            base_robust, v1_gate = robust_score(v1_metrics)
            if base_robust is not None:
                break
            log("v1 baseline had a failed holdout (proxy?), retry in 15s")
            time.sleep(15)
        log(f"v1 holdouts: {[(m['profit_pct'], m['sortino']) for m in v1_metrics]} "
            f"robust_score={base_robust} gate={v1_gate}")
        state["robust_baseline_score"] = base_robust
        state["robust_baseline_v1_metrics"] = v1_metrics
        STATE.write_text(json.dumps(state, indent=2))
    if base_robust is None:
        log("v1 robust baseline unavailable after retries, abort")
        return
    best_robust = state.get("best_robust_score", base_robust)
    log(f"v1 baseline={base_robust:.2f}, current best to beat={best_robust:.2f}")

    if SEARCH_JSON.exists():
        SEARCH_JSON.unlink()

    if REUSE:
        fthypt_path = find_latest_search_fthypt()
        log(f"REUSE: {fthypt_path}")
    else:
        t0 = time.time()
        p = None
        for attempt in range(3):
            p = run([".venv/bin/freqtrade", "hyperopt", "--strategy", SEARCH, "--config", CONFIG,
                     "--hyperopt-loss", LOSS, "--spaces", *SPACES, "--epochs", str(EPOCHS),
                     "-j", "8", "--min-trades", "100", "--timerange", TRAIN,
                     "--random-state", str(SEED), "--enable-protections"])
            log(f"hyperopt attempt {attempt+1} rc={p.returncode} elapsed={int(time.time()-t0)}s")
            if p.returncode == 0:
                break
            time.sleep(30)
        if p.returncode != 0:
            log("HYPEROPT FAILED")
            return
        fthypt_path = find_latest_search_fthypt()

    top = load_top_k(fthypt_path)
    if not top:
        log("no valid epochs")
        return
    log(f"evaluating top {len(top)} on {len(HOLDOUTS)} holdouts each...")

    candidates = []
    for rank, (pd, tm) in enumerate(top):
        ft = params_to_ft(pd, SEARCH)
        SEARCH_JSON.write_text(json.dumps(ft, indent=2))
        holdout_metrics = [backtest_window(SEARCH, s, e) for s, e in HOLDOUTS]
        score, gate = robust_score(holdout_metrics)
        profits = [m["profit_pct"] for m in holdout_metrics]
        log(f"  rank{rank} train_sortino={tm.get('sortino'):.2f} holdout_profits={[round(p,1) if p is not None else None for p in profits]} "
            f"robust_score={round(score,2) if score else None} gate={gate}")
        if gate and score is not None and score > best_robust:
            candidates.append({"pd": pd, "score": score, "holdouts": holdout_metrics})

    if SEARCH_JSON.exists():
        SEARCH_JSON.unlink()

    saved = None
    if candidates:
        win = max(candidates, key=lambda c: c["score"])
        n = next_version()
        name = f"EfutureLongKelvin_v{n}"
        src = SEARCH_PY.read_text().replace(f"class {SEARCH}(IStrategy):", f"class {name}(IStrategy):")
        (LONG_DIR / f"{name}.py").write_text(src)
        (LONG_DIR / f"{name}.json").write_text(json.dumps(params_to_ft(win["pd"], name), indent=2))
        saved = name
        state["best_robust_score"] = win["score"]
        STATE.write_text(json.dumps(state, indent=2))
        log(f"SAVED {name} robust_score={win['score']:.2f} (>prev best {best_robust:.2f}) "
            f"holdout_profits={[round(m['profit_pct'],1) for m in win['holdouts']]}")

    log(f"=== robust iter {ITER} DONE: candidates={len(candidates)}/{len(top)} saved={saved} ===")
    print(json.dumps({"iter": ITER, "candidates": len(candidates), "saved": saved,
                      "v1_robust": base_robust}))


if __name__ == "__main__":
    main()
