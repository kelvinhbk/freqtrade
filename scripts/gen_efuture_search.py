"""Generate v1-neighborhood search strategy for EfutureLongKelvin loop optimization.

Keeps EfutureLongKelvin.py code byte-identical except: class name, minimal_roi,
stoploss, and each Parameter's (low, high, default) tightened to a +/- SPAN_FRAC
neighborhood around the v1 value (clamped to the original range).

v1's buy params equal the original defaults (buy was never optimized), so buy
gets a default-neighborhood search while sell/roi/stoploss get v1-neighborhood.
"""
import json
import re
from pathlib import Path

STRAT_DIR = Path("user_data/strategies/efuture-long-kelvin")
SRC = STRAT_DIR / "EfutureLongKelvin.py"
V1_JSON = STRAT_DIR / "EfutureLongKelvin_v1.json"
OUT = STRAT_DIR / "EfutureLongKelvin_search.py"
SPAN_FRAC = 0.20  # neighborhood half-width as fraction of original (low,high) span

v1 = json.loads(V1_JSON.read_text())
buy = v1["params"]["buy"]
sell = v1["params"]["sell"]
roi = v1["params"]["roi"]
stoploss = v1["params"]["stoploss"]["stoploss"]

src = SRC.read_text()

# 1. class name
assert "class EfutureLongKelvin(IStrategy):" in src
src = src.replace("class EfutureLongKelvin(IStrategy):",
                  "class EfutureLongKelvin_search(IStrategy):")

# 2. minimal_roi -> v1 roi (first un-commented block only)
roi_line = "    minimal_roi = " + json.dumps(roi)
src, n_roi = re.subn(r'(?ms)^    minimal_roi = \{.*?\}', roi_line, src, count=1)
assert n_roi == 1, f"minimal_roi replace failed (n={n_roi})"

# 3. stoploss -> v1 stoploss
src, n_sl = re.subn(r'(?m)^    stoploss = -?[\d.]+',
                    f"    stoploss = {stoploss}", src, count=1)
assert n_sl == 1, f"stoploss replace failed (n={n_sl})"

# 4. Parameter (low, high, default) -> v1-neighborhood
param_re = re.compile(
    r'(?P<indent>[ \t]+)(?P<name>\w+) = (?P<ptype>IntParameter|DecimalParameter)\('
    r'\s*(?P<low>-?[\d.]+)\s*,\s*(?P<high>-?[\d.]+)\s*,\s*'
    r'default=(?P<def>-?[\d.]+)\s*,\s*'
    r'(?:decimals=(?P<dec>\d+)\s*,\s*)?'
    r'space="(?P<space>\w+)"[^)]*\)'
)

v1map = {**buy, **sell}  # name -> v1 value (buy=default, sell=optimized)
rows = []


def repl(m):
    name = m.group("name")
    ptype = m.group("ptype")
    low = float(m.group("low"))
    high = float(m.group("high"))
    dec = m.group("dec")
    space = m.group("space")
    indent = m.group("indent")
    if name not in v1map:
        rows.append((name, space, low, high, low, high, "UNCHANGED"))
        return m.group(0)
    center = float(v1map[name])
    span = high - low
    delta = SPAN_FRAC * span
    nlow = max(low, center - delta)
    nhigh = min(high, center + delta)
    if ptype == "IntParameter":
        nl, nh, nc = int(round(nlow)), int(round(nhigh)), int(round(center))
        if nl >= nh:
            nl = max(int(low), nc - 1)
            nh = min(int(high), nc + 1)
        rows.append((name, space, low, high, nl, nh, nc))
        return (f'{indent}{name} = IntParameter({nl}, {nh}, default={nc}, '
                f'space="{space}", optimize=True)')
    else:
        d = int(dec) if dec else 3
        if nlow >= nhigh:
            nlow = max(low, center - delta * 0.5)
            nhigh = min(high, center + delta * 0.5)
        nlow = max(low, nlow)
        nhigh = min(high, nhigh)
        rows.append((name, space, low, high, round(nlow, d), round(nhigh, d), round(center, d)))
        return (f'{indent}{name} = DecimalParameter({nlow:.{d}f}, {nhigh:.{d}f}, '
                f'default={center:.{d}f}, decimals={d}, space="{space}", optimize=True)')


src, n_params = param_re.subn(repl, src)
OUT.write_text(src)

print(f"Wrote {OUT}")
print(f"minimal_roi={json.dumps(roi)}  stoploss={stoploss}  params_substituted={n_params}")
print(f"\n{'name':<26}{'space':<6}{'orig_low':>10}{'orig_high':>10}{'new_low':>10}{'new_high':>10}{'center':>10}")
for name, space, ol, oh, nl, nh, nc in rows:
    print(f"{name:<26}{space:<6}{ol:>10}{oh:>10}{nl:>10}{nh:>10}{nc:>10}")
