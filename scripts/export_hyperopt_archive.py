"""Export top-5 epochs from each hyperopt result file to small JSON archives.

Usage: python scripts/export_hyperopt_archive.py
Output: user_data/archive/params-archive/<filename-stem>.json

.fthypt files are line-delimited JSON (one epoch per line, up to ~1.5MB/line,
mostly results_metrics.trades). To avoid parsing the huge per-epoch trade
details, a fast path extracts only "loss" and "params_dict"; it falls back to
a full json.loads of the line if the structure is unexpected.
"""
import heapq
import json
import math
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HYPEROPT_DIR = ROOT / "user_data" / "hyperopt_results"
OUT_DIR = ROOT / "user_data" / "archive" / "params-archive"
TOP_N = 5

LOSS_RE = re.compile(r'^\{"loss":\s*([^,]+),')
PARAMS_END_KEY = '"params_details":'


def _fast_parse_line(line: str) -> tuple[float | None, dict] | None:
    """Extract (loss, params_dict) without parsing the whole line.

    Returns None if the line doesn't match the expected layout,
    so the caller can fall back to a full parse.
    """
    m = LOSS_RE.match(line)
    if not m:
        return None
    try:
        loss = float(m.group(1))
    except ValueError:
        loss = None  # NaN/Infinity tokens
    start = line.find('"params_dict":')
    if start == -1:
        return None
    start += len('"params_dict":')
    end = line.find(PARAMS_END_KEY, start)
    if end == -1:
        return None
    params_raw = line[start:end].strip()
    if params_raw.endswith(","):
        params_raw = params_raw[:-1]
    params = json.loads(params_raw)
    if not isinstance(params, dict):
        return None
    return loss, params


def _full_parse_line(line: str) -> tuple[float | None, dict]:
    epoch = json.loads(line)
    if not isinstance(epoch, dict):
        return None, {}
    loss = epoch.get("loss")
    if not isinstance(loss, int | float) or (isinstance(loss, float) and not math.isfinite(loss)):
        loss = None
    params = epoch.get("params_dict") or epoch.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    return loss, params


def _iter_epoch_lines(path: Path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            name = [n for n in zf.namelist() if n.endswith(".json")][0]
            for line in zf.read(name).decode("utf-8").splitlines():
                if line.strip():
                    yield line
    else:
        with path.open("r") as f:
            for line in f:
                if line.strip():
                    yield line


def export_one(path: Path) -> dict:
    """Extract top-N epochs by loss from a .fthypt file (zip or plain)."""
    total_epochs = 0
    # min-top-N via max-heap on (-loss)
    best: list[tuple[float, int, dict]] = []
    for line in _iter_epoch_lines(path):
        total_epochs += 1
        parsed = _fast_parse_line(line)
        if parsed is None:
            parsed = _full_parse_line(line)
        loss, params = parsed
        if loss is None:
            continue
        item = (-loss, total_epochs, params)
        if len(best) < TOP_N:
            heapq.heappush(best, item)
        elif -loss > best[0][0]:
            heapq.heapreplace(best, item)
    top = sorted(best, key=lambda x: -x[0])
    return {
        "source_file": path.name,
        "total_epochs": total_epochs,
        "top": [{"loss": -neg_loss, "params": params} for neg_loss, _, params in top],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(HYPEROPT_DIR.glob("*.fthypt*"))
    ok = 0
    for i, f in enumerate(files, 1):
        try:
            archive = export_one(f)
        except Exception as exc:
            print(f"SKIP {f.name}: {exc}", flush=True)
            continue
        out = OUT_DIR / f"{f.stem}.json"
        out.write_text(json.dumps(archive, indent=2, default=str))
        ok += 1
        print(
            f"[{i}/{len(files)}] OK {f.name} -> {out.name} ({archive['total_epochs']} epochs)",
            flush=True,
        )
    print(f"Done: {ok}/{len(files)} exported, {len(list(OUT_DIR.glob('*.json')))} archives total")


if __name__ == "__main__":
    main()
