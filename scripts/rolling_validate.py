"""Rolling-window stability validation for EfutureLongKelvin_v10.

Runs v10 across N overlapping 6-month windows (2-month step) over the full
data range, records per-window metrics, and computes cross-window Sortino
mean/std/CV. Pass criteria: all windows PF>1.0 (profitable), CV<0.25 (must)
/ <0.15 (preferred).
"""
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Users/kelvin/projects/freqtrade")
STRATEGY = sys.argv[1] if len(sys.argv) > 1 else "EfutureLongKelvin_v10"
CONFIG = "user_data/config_EfutureIter_kelvin.json"
WINDOWS = [
    ("20240601", "20241130"),
    ("20240801", "20250131"),
    ("20241001", "20250331"),
    ("20241201", "20250531"),
    ("20250201", "20250731"),
    ("20250401", "20250930"),
    ("20250601", "20251130"),
    ("20250801", "20260131"),
    ("20251001", "20260331"),
    ("20251201", "20260611"),
]


def _grab(stdout, pat):
    m = re.search(pat, stdout)
    return m.group(1) if m else None


def parse_metrics(stdout):
    profit = _grab(stdout, r"Total profit %\s*[│|]\s*([\-\d.]+)%")
    sortino = _grab(stdout, r"Sortino\s*[│|]\s*([\-\d.]+)")
    sharpe = _grab(stdout, r"Sharpe\s*[│|]\s*([\-\d.]+)")
    calmar = _grab(stdout, r"Calmar\s*[│|]\s*([\-\d.]+)")
    pf = _grab(stdout, r"Profit factor\s*[│|]\s*([\-\d.]+)")
    dd = _grab(stdout, r"Absolute drawdown\s*[│|].*?\(([\d.]+)%\)")
    trades = _grab(stdout, r"Total/Daily Avg Trades\s*[│|]\s*(\d+)")
    return {
        "profit_pct": float(profit) if profit else None,
        "sortino": float(sortino) if sortino else None,
        "sharpe": float(sharpe) if sharpe else None,
        "calmar": float(calmar) if calmar else None,
        "profit_factor": float(pf) if pf else None,
        "dd_pct": float(dd) / 100 if dd else None,
        "trades": int(trades) if trades else None,
    }


def backtest(tr_start, tr_end):
    cmd = [".venv/bin/freqtrade", "backtesting", "--strategy", STRATEGY, "--config",
           CONFIG, "--timerange", f"{tr_start}-{tr_end}", "--fee", "0.0005",
           "--enable-protections"]
    for attempt in range(4):
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        m = parse_metrics(p.stdout)
        if m["profit_pct"] is not None:
            return m
        print(f"  [{tr_start}-{tr_end}] attempt {attempt+1} no metrics (proxy?), retry in 8s", flush=True)
        time.sleep(8)
    return m


def main():
    print(f"=== Rolling validation: {STRATEGY} across {len(WINDOWS)} windows ===\n")
    print(f"{'window':<23}{'profit%':>9}{'sortino':>9}{'pf':>7}{'dd%':>7}{'trades':>8}{'calmar':>9}")
    results = []
    for tr_start, tr_end in WINDOWS:
        m = backtest(tr_start, tr_end)
        results.append((f"{tr_start}-{tr_end}", m))
        dd = f"{m['dd_pct']*100:.1f}" if m['dd_pct'] is not None else "NA"
        print(f"{tr_start}-{tr_end:<17}"
              f"{m['profit_pct']!s:>9}{m['sortino']!s:>9}{m['profit_factor']!s:>7}"
              f"{dd:>7}{m['trades']!s:>8}{m['calmar']!s:>9}", flush=True)

    sortinos = [m["sortino"] for _, m in results if m["sortino"] is not None]
    pfs = [m["profit_factor"] for _, m in results if m["profit_factor"] is not None]
    profits = [m["profit_pct"] for _, m in results if m["profit_pct"] is not None]

    print(f"\n=== Cross-window statistics ({len(sortinos)} valid windows) ===")
    if len(sortinos) >= 2:
        mu = statistics.mean(sortinos)
        sigma = statistics.stdev(sortinos)
        cv = sigma / mu if mu != 0 else float("inf")
        print(f"Sortino: mean={mu:.2f}  std={sigma:.2f}  CV={cv:.3f}")
        print(f"  CV<0.25 (must): {'PASS' if cv < 0.25 else 'FAIL'}")
        print(f"  CV<0.15 (preferred): {'PASS' if cv < 0.15 else 'FAIL'}")
    print(f"Profit factor: min={min(pfs):.2f}  mean={statistics.mean(pfs):.2f}")
    print(f"  All windows PF>1.0: {'PASS' if all(p > 1.0 for p in pfs) else 'FAIL'}")
    nprof = sum(1 for p in profits if p > 0)
    print(f"Profitable windows: {nprof}/{len(profits)}")
    print(f"Profit range: {min(profits):.2f}% ~ {max(profits):.2f}%")
    nneg = sum(1 for p in profits if p <= 0)
    if nneg:
        print(f"  WARNING: {nneg} window(s) non-profitable -> possible overfit/instability")


if __name__ == "__main__":
    main()
