"""Single iteration of the EfutureLongKelvin loop optimization.

Flow: hyperopt (v1-neighborhood, buy/sell/stoploss spaces; roi fixed to v1) ->
parse best params from .fthypt -> OOS backtest -> compare to baseline ->
save new version to efuture-long-kelvin/ if improved -> update state.

Usage:
    python run_iteration.py <iteration> <seed> [--dry-run]
Dry-run skips hyperopt and reuses the existing v1 .fthypt to validate the pipeline.

roi is intentionally kept OUT of hyperopt spaces: v1's low-threshold roi is the
core of the "let profits run" design, and the .fthypt roi_t/roi_p encoding does
not map cleanly back to minimal_roi. The strategy class keeps v1's roi.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ITER = int(sys.argv[1])
SEED = int(sys.argv[2])
DRY_RUN = "--dry-run" in sys.argv
REUSE = "--reuse" in sys.argv  # reuse latest search fthypt, skip hyperopt

ROOT = Path("/Users/kelvin/projects/freqtrade")
STATE = ROOT / "user_data/strategies/efuture-long-kelvin/_loop_state.json"
CONFIG = "user_data/configs/dryrun/EfutureIter.json"
SEARCH = "EfutureLongKelvin_search"
SEARCH_PY = ROOT / f"user_data/strategies/efuture-long-kelvin/{SEARCH}.py"
SEARCH_JSON = ROOT / f"user_data/strategies/efuture-long-kelvin/{SEARCH}.json"
LONG_DIR = ROOT / "user_data/strategies/efuture-long-kelvin"
FTHYPT = f"{SEARCH}_{ITER}.fthypt"
TRAIN = "20240801-20260430"
OOS = "20260501-20260611"
EPOCHS = 400
LOSS = "SortinoHyperOptLossDaily"
SPACES = ["buy", "sell"]  # roi + stoploss fixed (v1 -0.287 lets custom_stoploss lead)
MIN_TRADES_OOS = 30

BASELINE = {"profit_pct": 13.17, "sortino": 3.23, "sharpe": 3.59,
            "profit_factor": 1.35, "max_drawdown": 0.1507, "trades": 69}
BASELINE_SCORE = (BASELINE["sortino"] * 10 + BASELINE["profit_pct"]
                  - BASELINE["max_drawdown"] * 30 + (BASELINE["profit_factor"] - 1) * 20)

LOG = LONG_DIR / f"iter_{ITER}.log"


def log(msg):
    line = f"[iter {ITER}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def run(cmd):
    log("RUN: " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    with LOG.open("a") as f:
        f.write(p.stdout + "\n" + p.stderr + "\n")
    return p


def parse_metrics(stdout):
    def grab(pat):
        m = re.search(pat, stdout)
        return m.group(1) if m else None
    profit = grab(r"Total profit %\s*[│|]\s*([\-\d.]+)%")
    sortino = grab(r"Sortino\s*[│|]\s*([\-\d.]+)")
    sharpe = grab(r"Sharpe\s*[│|]\s*([\-\d.]+)")
    pf = grab(r"Profit factor\s*[│|]\s*([\-\d.]+)")
    dd = grab(r"Absolute drawdown\s*[│|].*?\(([\d.]+)%\)")
    trades = grab(r"Total/Daily Avg Trades\s*[│|]\s*(\d+)")
    calmar = grab(r"Calmar\s*[│|]\s*([\-\d.]+)")
    return {
        "profit_pct": float(profit) if profit else None,
        "sortino": float(sortino) if sortino else None,
        "sharpe": float(sharpe) if sharpe else None,
        "profit_factor": float(pf) if pf else None,
        "max_drawdown": float(dd) / 100 if dd else None,
        "trades": int(trades) if trades else None,
        "calmar": float(calmar) if calmar else None,
    }


def composite(m):
    if not all([m.get("profit_pct") is not None, m.get("sortino"),
                m.get("profit_factor"), m.get("max_drawdown") is not None]):
        return None
    return (m["sortino"] * 10 + m["profit_pct"] - m["max_drawdown"] * 30
            + (m["profit_factor"] - 1) * 20)


def is_improved(oos):
    if not oos.get("profit_pct") or not oos.get("sortino"):
        return False, "missing metrics"
    if oos["profit_pct"] <= BASELINE["profit_pct"]:
        return False, f"profit {oos['profit_pct']:.2f}% <= baseline {BASELINE['profit_pct']}%"
    if oos["sortino"] < BASELINE["sortino"] * 0.8:
        return False, f"sortino {oos['sortino']:.2f} < {BASELINE['sortino']*0.8:.2f}"
    if oos.get("profit_factor", 0) < 1.2:
        return False, f"PF {oos['profit_factor']:.2f} < 1.2"
    if oos.get("max_drawdown", 1) > 0.20:
        return False, f"DD {oos['max_drawdown']*100:.1f}% > 20%"
    if oos.get("trades", 0) < MIN_TRADES_OOS:
        return False, f"trades {oos['trades']} < {MIN_TRADES_OOS}"
    return True, "improved"


def next_version():
    n = 2  # v1/v2 already exist in strategies/
    for d in [ROOT / "user_data/strategies", LONG_DIR]:
        for f in d.glob("EfutureLongKelvin_v*.py"):
            m = re.search(r"_v(\d+)\.py$", f.name)
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


def params_to_ft(pd, name):
    """Build freqtrade strategy param json from a params_dict (buy/sell/stoploss).
    roi is omitted so the strategy class default (v1 roi) is used."""
    buy_p = {k: v for k, v in pd.items() if k.startswith("buy_")}
    sell_p = {k: v for k, v in pd.items()
              if not k.startswith("buy_") and not k.startswith("roi_") and k != "stoploss"}
    return {
        "strategy_name": name,
        "params": {
            "trailing": {
                "trailing_stop": False,
                "trailing_stop_positive": None,
                "trailing_stop_positive_offset": 0.0,
                "trailing_only_offset_is_reached": False,
            },
            "max_open_trades": {"max_open_trades": 3},
            "buy": buy_p,
            "sell": sell_p,
            "stoploss": {"stoploss": pd.get("stoploss", -0.287)},
        },
        "ft_stratparam_v": 1,
    }


def save_version(pd, oos, score):
    n = next_version()
    name = f"EfutureLongKelvin_v{n}"
    dst_py = LONG_DIR / f"{name}.py"
    dst_json = LONG_DIR / f"{name}.json"
    src = SEARCH_PY.read_text()
    src = src.replace(f"class {SEARCH}(IStrategy):", f"class {name}(IStrategy):")
    dst_py.write_text(src)
    dst_json.write_text(json.dumps(params_to_ft(pd, name), indent=2))
    log(f"SAVED {name} (OOS profit {oos['profit_pct']:.2f}%, sortino {oos['sortino']:.2f}, score {score:.2f})")
    return name


def find_latest_search_fthypt():
    """Newest hyperopt result file for the search strategy (auto-named by freqtrade)."""
    files = sorted((ROOT / "user_data/hyperopt_results").glob(
        "strategy_EfutureLongKelvin_search_*.fthypt"), key=lambda f: f.stat().st_mtime)
    return files[-1].name if files else None


def load_top_k(fthypt_name, k=5):
    """Return top-k (params_dict, loss, results_metrics) sorted by min loss (valid trials only)."""
    f = ROOT / f"user_data/hyperopt_results/{fthypt_name}"
    lines = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    valid = [l for l in lines
             if l.get("loss", 100) < 99 and l.get("results_metrics", {}).get("total_trades", 0) > 0]
    # sort by backtest sortino (same metric as v1 baseline), NOT loss (daily sortino)
    valid.sort(key=lambda x: -x["results_metrics"].get("sortino", -999.0))
    return [(l["params_dict"], l["loss"], l["results_metrics"]) for l in valid[:k]]


def main():
    LOG.unlink(missing_ok=True)
    log(f"=== iteration {ITER} seed {SEED} dry_run={DRY_RUN} ===")

    if SEARCH_JSON.exists():
        SEARCH_JSON.unlink()

    if DRY_RUN:
        fthypt_path = "strategy_EfutureLongKelvin_v1_2026-06-13_17-41-03.fthypt"
        log(f"DRY-RUN: reusing {fthypt_path}")
    elif REUSE:
        fthypt_path = find_latest_search_fthypt()
        log(f"REUSE: reusing {fthypt_path}")
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
            log("hyperopt failed (proxy/markets?), retry in 30s")
            time.sleep(30)
        if p.returncode != 0:
            log("HYPEROPT FAILED after retries")
            _update_state(ITER, status="hyperopt_failed")
            return
        fthypt_path = find_latest_search_fthypt()
        if not fthypt_path:
            log("NO FTHYPT produced by hyperopt")
            _update_state(ITER, status="no_fthypt")
            return
        log(f"using fthypt: {fthypt_path}")

    top = load_top_k(fthypt_path, k=5)
    if not top:
        log("NO VALID EPOCHS in fthypt")
        _update_state(ITER, status="no_best")
        return
    log(f"evaluating top {len(top)} epochs (by loss) on OOS {OOS}")
    candidates = []
    for rank, (pd, loss, tm) in enumerate(top):
        ft = params_to_ft(pd, SEARCH)
        SEARCH_JSON.write_text(json.dumps(ft, indent=2))
        oos = {}
        for attempt in range(3):
            bt = run([".venv/bin/freqtrade", "backtesting", "--strategy", SEARCH, "--config", CONFIG,
                      "--timerange", OOS, "--fee", "0.0005", "--enable-protections"])
            oos = parse_metrics(bt.stdout)
            if oos.get("profit_pct") is not None:
                break
            log(f"  rank{rank} backtest attempt {attempt+1} no metrics (proxy/markets), retry in 5s")
            time.sleep(5)
        score = composite(oos)
        improved, reason = is_improved(oos)
        ratio = (oos["sortino"] / tm["sortino"]) if (tm.get("sortino") and oos.get("sortino")) else None
        log(f"  rank{rank} loss={loss:.3f} train_sortino={tm.get('sortino')} "
            f"OOS profit={oos.get('profit_pct')} sortino={oos.get('sortino')} "
            f"pf={oos.get('profit_factor')} dd={oos.get('max_drawdown')} "
            f"score={score} improved={improved}")
        if improved:
            candidates.append({"pd": pd, "oos": oos, "score": score,
                               "train_oos_ratio": ratio})
    if SEARCH_JSON.exists():
        SEARCH_JSON.unlink()

    saved = None
    best_oos = None
    best_score = None
    best_ratio = None
    best_reason = f"none of top {len(top)} improved"
    if candidates:
        win = max(candidates, key=lambda c: c["score"])
        saved = save_version(win["pd"], win["oos"], win["score"])
        best_oos = win["oos"]
        best_score = win["score"]
        best_ratio = win["train_oos_ratio"]
        best_reason = "improved"

    _update_state(ITER, seed=SEED, oos=best_oos, score=best_score,
                  improved=bool(candidates), reason=best_reason, saved=saved,
                  train_oos_ratio=best_ratio, status="ok")
    log(f"=== iteration {ITER} DONE: candidates={len(candidates)}/{len(top)} saved={saved} ===")
    print(json.dumps({"iter": ITER, "candidates": len(candidates), "evaluated": len(top),
                      "saved": saved, "best_oos": best_oos, "best_score": best_score}))


def _update_state(iter_n, seed=None, oos=None, score=None,
                  improved=None, reason=None, saved=None,
                  train_oos_ratio=None, status=None):
    if not STATE.exists():
        STATE.write_text(json.dumps({
            "task": "EfutureLongKelvin loop optimization",
            "total_iterations": 20, "iteration": 1, "baseline": BASELINE,
            "baseline_score": round(BASELINE_SCORE, 3), "best": None,
            "best_score": round(BASELINE_SCORE, 3), "best_oos": None, "history": [],
            "config": {"search_strategy": SEARCH, "config_file": CONFIG,
                       "train_range": TRAIN, "oos_range": OOS, "epochs": EPOCHS,
                       "loss": LOSS, "spaces": SPACES},
        }, indent=2))
    state = json.loads(STATE.read_text())
    entry = {"iter": iter_n, "status": status}
    if seed is not None:
        entry["seed"] = seed
    if oos:
        entry["oos"] = oos
    if score is not None:
        entry["score"] = round(score, 3) if score else None
    if improved is not None:
        entry["improved"] = improved
    if reason:
        entry["reason"] = reason
    if saved:
        entry["saved"] = saved
    if train_oos_ratio is not None:
        entry["train_oos_ratio"] = round(train_oos_ratio, 3)
    state["history"].append(entry)
    state["iteration"] = iter_n + 1
    if improved and score and score > state["best_score"]:
        state["best"] = saved
        state["best_score"] = round(score, 3)
        state["best_oos"] = oos
    STATE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
