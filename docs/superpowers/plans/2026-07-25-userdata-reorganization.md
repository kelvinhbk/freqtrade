# user_data 重组实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-07-25-userdata-reorganization-design.md` 将 user_data 重组为策略线结构，全量入 git，清理 195G+ 产物。

**Architecture:** 策略按线分目录（efuture-iter / efuture-long / efuture-long-kelvin / efuture-short / enew / freqai-v6 / experiments），config 按环境三分（live/dryrun/backtest），密钥抽取到 gitignored 本地文件，产物三层保留（结论进 git / 证据留盘 / 过程删除）。全程 git mv 保历史，实盘 bot 最后切换。

**Tech Stack:** git、freqtrade CLI、Python 3.11+、python3 json

**Spec:** `docs/superpowers/specs/2026-07-25-userdata-reorganization-design.md`（已批准，commit 4875ccf3a）

## Global Constraints

- 所有文件移动必须用 `git mv`（未跟踪文件 git mv 会失败，fallback 普通 `mv` 是正确行为——首次 add 即入历史）
- 禁止将任何密钥（Telegram token、API password、交易所 key/secret）提交进 git
- 实盘进程（EfutureIter，dry_run=false）在 Task 10 之前不得中断
- 删除任何目录前先确认其内容已在 git 历史或 archive/params-archive/ 中
- commit message 用 conventional commits，中文描述
- 每条命令工作目录为 `/Users/kelvin/projects/freqtrade`

## 已知事实（执行时无需重新探查）

- `.gitignore:7` 为 `user_data/*`，第 8-19 行为豁免规则；`__pycache__/` 在第 29 行已全局忽略
- 硬编码路径引用：`scripts/run_efuture_loop.py:28-33`、`scripts/run_robust_loop.py:24-29`、`user_data/autoresearch/evolution_config_{efuture,efuture_iter,efuture_short,enew,enew_exit_only,enew_smoke,macd_v4}.json` 的 `strategy_path`/`strategy_output_dir`
- `evolution_config_enew*.json` 引用的 `user_data/strategies/ENEW.py`、`evolution_config_macd_v4.json` 引用的 `user_data/strategies/macd_v4.py` 当前已不存在（历史遗留，本次只改路径不修复实验）
- live config 变体差异：`live_opt` 缺 RENDER 交易对；`live_opt_noproxy` 缺 RENDER + 无 proxy 配置；`kelvin` 实为 dry_run=true 的本地模拟盘配置（无 telegram、API 监听 127.0.0.1）
- `autoresearch0515-1` 与 `autoresearch0515-2` 已 diff 确认完全相同
- TopStr 的 E0033 与 before_live_bot 快照逐字节相同，是 EfutureIter 的祖先

---

### Task 1: git 基线 + .gitignore 改造

**Files:**
- Modify: `.gitignore:7-19`

**Interfaces:**
- Produces: 后续所有任务的 git 跟踪基础；豁免规则清单（strategies/、configs/、archive/；scripts/ 在仓库根已被跟踪）

- [ ] **Step 1: 提交未跟踪脚本，工作区归零**

```bash
git status --porcelain
git add scripts/gen_efuture_search.py scripts/rolling_validate.py scripts/run_efuture_loop.py scripts/run_robust_loop.py
git commit -m "feat(scripts): EfutureLongKelvin 循环优化编排脚本入库"
```
预期：commit 后 `git status --porcelain` 无输出。

- [ ] **Step 2: 改写 .gitignore 的 user_data 段**

将 `.gitignore:7-19` 整段替换为：

```gitignore
# user_data: 默认忽略，按目录豁免（代码/配置进 git，数据/产物忽略）
user_data/*
!user_data/strategies/
!user_data/strategies/**
!user_data/configs/
!user_data/configs/**
!user_data/archive/
!user_data/archive/**
!user_data/autoresearch/
!user_data/autoresearch/**
!user_data/notebooks
!user_data/models
!user_data/freqaimodels
!user_data/README.md

# 密钥与本地私有配置永不入库
user_data/configs/secrets.local.json
user_data/autoresearch/.env

# 产物与数据忽略（即使在被豁免目录内）
user_data/autoresearch/results/
user_data/autoresearch/**/*.log
user_data/strategies/**/_loop_state.json
user_data/freqaimodels/*
user_data/models/*
user_data/notebooks/*
```

注：`_loop_state.json` 是循环运行时状态，不入库；optimization_report.md 等结论文档在豁免目录内自然入库。

- [ ] **Step 3: 验证忽略规则**

```bash
git status --porcelain user_data/ | head -30
git ls-files --others --exclude-standard user_data/ | xargs -I{} du -k {} 2>/dev/null | sort -rn | head -5
```
预期：未跟踪列表出现 strategies/ 下 .py/.json，不出现 results/、hyperopt_results/、.fthypt、.env、secrets.local.json；最大新增文件 < 1024KB。

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore 重构——user_data 代码/配置入库，产物/密钥排除"
```

---

### Task 2: 密钥抽取到 secrets.local.json

**Files:**
- Create: `user_data/configs/secrets.local.json`（gitignored）
- Modify: `user_data/config_EfutureIter_live.json`、`user_data/config_EfutureIter_kelvin.json`

**Interfaces:**
- Produces: `user_data/configs/secrets.local.json`，结构为 `{"telegram": {"enabled": bool, "token": str, "chat_id": str}, "api_server": {"username": str, "password": str}, "exchange": {"key": str, "secret": str}}`；后续启动命令形态为 `freqtrade trade -c <主config> -c user_data/configs/secrets.local.json`（freqtrade 多 -c 后者覆盖前者）

- [ ] **Step 1: 从现有 config 抽取密钥生成 secrets.local.json**

```bash
python3 - <<'EOF'
import json, pathlib
base = pathlib.Path("user_data")
live = json.loads((base/"config_EfutureIter_live.json").read_text())
secrets = {
    "telegram": {"enabled": live["telegram"]["enabled"],
                 "token": live["telegram"].get("token",""),
                 "chat_id": live["telegram"].get("chat_id","")},
    "api_server": {"username": live["api_server"].get("username",""),
                   "password": live["api_server"].get("password","")},
    "exchange": {"key": live["exchange"].get("key",""),
                 "secret": live["exchange"].get("secret","")},
}
(base/"configs").mkdir(exist_ok=True)
(base/"configs"/"secrets.local.json").write_text(json.dumps(secrets, indent=4))
print("written, sections:", list(secrets))
EOF
```
预期：打印 `written, sections: ['telegram', 'api_server', 'exchange']`。

- [ ] **Step 2: 清空主 config 中的密钥字段**

```bash
python3 - <<'EOF'
import json, pathlib
for name in ["config_EfutureIter_live.json", "config_EfutureIter_kelvin.json"]:
    p = pathlib.Path("user_data")/name
    cfg = json.loads(p.read_text())
    for sect, keys in [("telegram",["token","chat_id"]),
                       ("api_server",["username","password"]),
                       ("exchange",["key","secret"])]:
        for k in keys:
            if sect in cfg and k in cfg[sect]:
                cfg[sect][k] = ""
    p.write_text(json.dumps(cfg, indent=4) + "\n")
    print("scrubbed:", name)
EOF
```

- [ ] **Step 3: 验证无残留密钥且 secrets 被忽略**

```bash
grep -rn '"token": "[^"]\|"password": "[^"]\|"secret": "[^"]\|"key": "[^"]' user_data/config_*.json
git check-ignore user_data/configs/secrets.local.json && echo "secrets IGNORED ok"
```
预期：第一条无输出；第二条输出 `secrets IGNORED ok`。

- [ ] **Step 4: Commit**

```bash
git add user_data/config_EfutureIter_live.json user_data/config_EfutureIter_kelvin.json
git commit -m "security: 抽取 telegram/api/exchange 密钥到 gitignored secrets.local.json"
```

---

### Task 3: 导出 hyperopt 参数存档（删 195G 前的保险）

**Files:**
- Create: `scripts/export_hyperopt_archive.py`
- Create: `user_data/archive/params-archive/`（输出目录）

**Interfaces:**
- Produces: `user_data/archive/params-archive/<file-stem>.json`，结构 `{"source_file": str, "total_epochs": int, "top": [{"loss": float, "params": dict}]}`（top 最多 5 条）；Task 11 删除 hyperopt_results 前必须确认此目录非空

- [ ] **Step 1: 编写导出脚本**

```python
"""Export top-5 epochs from each hyperopt result file to small JSON archives.

Usage: python scripts/export_hyperopt_archive.py
Output: user_data/archive/params-archive/<filename-stem>.json
"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HYPEROPT_DIR = ROOT / "user_data" / "hyperopt_results"
OUT_DIR = ROOT / "user_data" / "archive" / "params-archive"
TOP_N = 5


def export_one(path: Path) -> dict:
    """Extract top-N epochs by loss from a .fthypt file (zip or plain json)."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            name = [n for n in zf.namelist() if n.endswith(".json")][0]
            data = json.loads(zf.read(name))
    else:
        data = json.loads(path.read_text())
    epochs = data if isinstance(data, list) else data.get("epochs", [])
    best = sorted(epochs, key=lambda e: e.get("loss", float("inf")))[:TOP_N]
    return {
        "source_file": path.name,
        "total_epochs": len(epochs),
        "top": [{"loss": e.get("loss"), "params": e.get("params", {})} for e in best],
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(HYPEROPT_DIR.glob("*.fthypt*"))
    for f in files:
        try:
            archive = export_one(f)
        except Exception as exc:
            print(f"SKIP {f.name}: {exc}")
            continue
        out = OUT_DIR / f"{f.stem}.json"
        out.write_text(json.dumps(archive, indent=2, default=str))
        print(f"OK {f.name} -> {out.name} ({archive['total_epochs']} epochs)")
    print(f"Done: {len(list(OUT_DIR.glob('*.json')))} archives")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行并验证**

```bash
python3 scripts/export_hyperopt_archive.py
ls user_data/archive/params-archive/ | wc -l
du -sh user_data/archive/params-archive/
```
预期：archive 数量接近 .fthypt 文件数（SKIP 需人工核对原因）；总大小 < 50MB。

- [ ] **Step 3: Commit**

```bash
git add scripts/export_hyperopt_archive.py user_data/archive/params-archive/
git commit -m "feat(scripts): hyperopt top-5 参数存档导出，为 195G 清理做保险"
```

---

### Task 4: 删除安全项（空壳/重复/pycache）

**Files:**
- Delete: `user_data/strategies/autoresearch_efuture_short/`、`user_data/strategies/autoresearch0515-2/`

- [ ] **Step 1: 三重确认**

```bash
ls -A user_data/strategies/autoresearch_efuture_short/ | grep -v __pycache__ | wc -l
diff -rq user_data/strategies/autoresearch0515-1 user_data/strategies/autoresearch0515-2 | grep -v __pycache__
```
预期：第一条输出 `0`；第二条无输出。

- [ ] **Step 2: 删除 + 全仓清理 pycache**

```bash
rm -rf user_data/strategies/autoresearch_efuture_short user_data/strategies/autoresearch0515-2
find user_data -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
```

- [ ] **Step 3: Commit**

```bash
git add -A user_data/strategies/
git commit -m "chore: 删除空壳实验目录 autoresearch_efuture_short 与重复目录 autoresearch0515-2"
```

---

### Task 5: strategies/ 按策略线重组

**Files:**
- Move: `user_data/strategies/` 下全部内容 → 各线子目录

**Interfaces:**
- Produces: 目标结构（Task 8 改引用以此为准）：
  - `efuture-iter/` ← EfutureIter.py/json
  - `efuture-long/` ← EfutureLong.py、EfutureLong_v1/v2/v4 .py/.json、EfutureLong_v4_hyperopt.json
  - `efuture-long-kelvin/` ← EfutureLongKelvin.py、_v1/_v2/_search .py/.json、Efuturelong/ 目录全部内容（v12 系列、report）
  - `efuture-short/` ← EfutureShort.py/json、EfutureShort_Iter14.py、best_strategies/ 内容
  - `enew/` ← 空目录占位（autoresearch_enew 产物在 experiments/）
  - `freqai-v6/` ← FreqaiExampleStrategy_v6.py + freaiStr/ 内容
  - `experiments/{autoresearch,autoresearch_efuture,autoresearch_enew,autoresearch_macd_v4,autoresearch0515-1}/`

- [ ] **Step 1: 建目录并逐线移动**

```bash
cd user_data/strategies
mkdir -p efuture-iter efuture-long efuture-long-kelvin efuture-short enew freqai-v6 experiments

mv EfutureIter.py EfutureIter.json efuture-iter/

for f in EfutureLong.py EfutureLong_v1.py EfutureLong_v1.json EfutureLong_v2.py EfutureLong_v2.json EfutureLong_v4.py EfutureLong_v4.json EfutureLong_v4_hyperopt.json; do
  [ -e "$f" ] && mv "$f" efuture-long/
done

for f in EfutureLongKelvin.py EfutureLongKelvin_v1.py EfutureLongKelvin_v1.json EfutureLongKelvin_v2.py EfutureLongKelvin_v2.json EfutureLongKelvin_search.py; do
  [ -e "$f" ] && mv "$f" efuture-long-kelvin/
done
mv Efuturelong/* efuture-long-kelvin/ && rmdir Efuturelong

for f in EfutureShort.py EfutureShort.json EfutureShort_Iter14.py; do
  [ -e "$f" ] && mv "$f" efuture-short/
done
mv best_strategies/* efuture-short/ && rmdir best_strategies

mv FreqaiExampleStrategy_v6.py freqai-v6/
mv ../freaiStr/* freqai-v6/ && rmdir ../freaiStr

for d in autoresearch autoresearch_efuture autoresearch_enew autoresearch_macd_v4 autoresearch0515-1; do
  [ -d "$d" ] && mv "$d" experiments/
done
cd ../..
```

- [ ] **Step 2: 验证 strategies/ 根干净**

```bash
ls -A user_data/strategies/ | grep -v "^efuture\|^enew$\|^freqai-v6$\|^experiments$"
```
预期：无输出。

- [ ] **Step 3: 冒烟——每条线策略可被 freqtrade 加载**

```bash
.venv/bin/freqtrade list-strategies --strategy-path user_data/strategies/efuture-iter -1
.venv/bin/freqtrade list-strategies --strategy-path user_data/strategies/efuture-long-kelvin -1
.venv/bin/freqtrade list-strategies --strategy-path user_data/strategies/efuture-short -1
.venv/bin/freqtrade list-strategies --strategy-path user_data/strategies/freqai-v6 -1
```
预期：各列出对应策略类名，无 import error。

- [ ] **Step 4: Commit**

```bash
git add -A user_data/strategies/
git commit -m "refactor(user_data): strategies 按策略线重组（6线+experiments）"
```

---

### Task 6: configs/ 集中（live/dryrun/backtest）

**Files:**
- Move: `user_data/config_*.json` → `user_data/configs/{live,dryrun,backtest}/`
- Delete: `user_data/config_EfutureIter_live_opt.json`、`user_data/config_EfutureIter_live_opt_noproxy.json`

**Interfaces:**
- Consumes: Task 2 的 `configs/secrets.local.json`、Task 5 的 freqai-v6/ 目录
- Produces: `configs/live/EfutureIter.json`（真身）、`configs/dryrun/EfutureIter.json`（原 kelvin）、`configs/backtest/EfutureLongKelvin.json`（原 opt_loop）、`configs/backtest/FreqaiV6.json`；Task 9 的 configs/README.md 与 Task 10 的实盘启动命令引用这些路径

- [ ] **Step 1: 删除两个 live 冗余变体**

差异已在"已知事实"确认：两者均为真身的子集变体，无独有有效配置；proxy 需求改由环境变量解决。

```bash
rm user_data/config_EfutureIter_live_opt.json user_data/config_EfutureIter_live_opt_noproxy.json
```

- [ ] **Step 2: 移动并重命名**

```bash
mkdir -p user_data/configs/{live,dryrun,backtest}
mv user_data/config_EfutureIter_live.json user_data/configs/live/EfutureIter.json
mv user_data/config_EfutureIter_kelvin.json user_data/configs/dryrun/EfutureIter.json
mv user_data/config_opt_loop.json user_data/configs/backtest/EfutureLongKelvin.json
mv user_data/strategies/freqai-v6/config_freqai_v6.json user_data/configs/backtest/FreqaiV6.json
mv user_data/strategies/freqai-v6/FreqaiExampleStrategy_v6_params.json user_data/strategies/freqai-v6/FreqaiExampleStrategy_v6.json
```

- [ ] **Step 3: 修正 config 内的路径字段**

```bash
grep -l "strategy_path\|user_data" user_data/configs/*/*.json
```
逐个检查输出文件的 `strategy_path`、`db_url`、`logfile` 等路径字段，改为新结构下的正确相对路径（如 `"strategy_path": "user_data/strategies/efuture-iter"`）。

- [ ] **Step 4: 验证——dryrun config 启动冒烟**

```bash
timeout 20 .venv/bin/freqtrade trade --config user_data/configs/dryrun/EfutureIter.json --config user_data/configs/secrets.local.json 2>&1 | head -20
```
预期：日志出现策略加载与 worker 启动，无 config validation error；20 秒自动退出。

- [ ] **Step 5: Commit**

```bash
git add -A user_data/configs/ user_data/config_EfutureIter_live_opt.json user_data/config_EfutureIter_live_opt_noproxy.json
git commit -m "refactor(configs): 按 live/dryrun/backtest 三分集中，删除冗余 live 变体"
```

---

### Task 7: archive/ 归档 + TopStr 撤销（谱系归位）

**Files:**
- Move: `user_data/originalStratergy/` → `user_data/archive/e0v1e/`；`user_data/backup/` → `user_data/archive/backup/`；`user_data/before_live_bot/` → `user_data/archive/before-live-bot/`
- Move: `user_data/TopStr/` 内容 → `user_data/strategies/efuture-iter/` 与 `user_data/configs/backtest/`

- [ ] **Step 1: 归档三个目录**

```bash
mv user_data/originalStratergy user_data/archive/e0v1e
mv user_data/backup user_data/archive/backup
mv user_data/before_live_bot user_data/archive/before-live-bot
```

- [ ] **Step 2: TopStr 谱系归位**

```bash
mv user_data/TopStr/AutoResearch_iter0002_E0010_E0014_E0033.py user_data/strategies/efuture-iter/
mv user_data/TopStr/AutoResearch_iter0002_E0010_E0014_E0033.json user_data/strategies/efuture-iter/
mv user_data/TopStr/E0033_审查报告.md user_data/strategies/efuture-iter/
mv user_data/TopStr/AutoResearch_iter0002.py user_data/TopStr/AutoResearch_iter0002.json user_data/strategies/efuture-iter/
mv user_data/TopStr/config_backtest.json user_data/configs/backtest/EfutureIter.lineage.json
rmdir user_data/TopStr
```

- [ ] **Step 3: 验证 user_data 根无遗留散落目录**

```bash
ls -d user_data/*/ | sort
```
预期仅剩：archive/、autoresearch/、backtest_results/、configs/、data/、dbs/、freqaimodels/、hyperopt_results/、hyperopts/、livebot/、logs/、models/、notebooks/、plot/、strategies/。

- [ ] **Step 4: Commit**

```bash
git add -A user_data/
git commit -m "refactor(archive): 退役产物归档，TopStr 谱系并入 efuture-iter 线"
```

---

### Task 8: 修正硬编码路径引用

**Files:**
- Modify: `scripts/run_efuture_loop.py:28-33`、`scripts/run_robust_loop.py:24-29`
- Modify: `user_data/autoresearch/evolution_config_{efuture,efuture_iter,efuture_short,enew,enew_exit_only,enew_smoke,macd_v4}.json`

**Interfaces:**
- Consumes: Task 5 的目标结构
- Produces: 循环脚本与 autoresearch 框架在新结构下可运行

- [ ] **Step 1: 改 scripts/run_efuture_loop.py 和 run_robust_loop.py**

两个文件做相同改动（原行内容见"已知事实"）：

```python
STATE = ROOT / "user_data/strategies/efuture-long-kelvin/_loop_state.json"
SEARCH_PY = ROOT / f"user_data/strategies/efuture-long-kelvin/{SEARCH}.py"
SEARCH_JSON = ROOT / f"user_data/strategies/efuture-long-kelvin/{SEARCH}.json"
LONG_DIR = ROOT / "user_data/strategies/efuture-long-kelvin"
```

SEARCH 变量值不变（`EfutureLongKelvin_search.py` 已随 Task 5 移入该目录）。

- [ ] **Step 2: 批量改 evolution_config_*.json 的输出目录**

```bash
cd user_data/autoresearch
python3 - <<'EOF'
import json, pathlib
mapping = {
    "user_data/strategies/autoresearch_efuture_short": "user_data/strategies/experiments/autoresearch_efuture_short",
    "user_data/strategies/autoresearch_efuture": "user_data/strategies/experiments/autoresearch_efuture",
    "user_data/strategies/autoresearch_enew": "user_data/strategies/experiments/autoresearch_enew",
    "user_data/strategies/autoresearch_macd_v4": "user_data/strategies/experiments/autoresearch_macd_v4",
    "user_data/strategies/autoresearch": "user_data/strategies/experiments/autoresearch",
}
for p in sorted(pathlib.Path(".").glob("evolution_config_*.json")):
    cfg = json.loads(p.read_text())
    changed = False
    for key in ("strategy_output_dir", "strategy_path"):
        if key in cfg:
            for old, new in mapping.items():
                if cfg[key].startswith(old):
                    cfg[key] = cfg[key].replace(old, new, 1)
                    changed = True
    if changed:
        p.write_text(json.dumps(cfg, indent=2) + "\n")
        print("updated:", p.name)
EOF
cd ../../..
```

注：mapping 中长键在前，避免 `autoresearch` 前缀抢先匹配 `autoresearch_efuture`。`strategy_path` 指向已不存在文件的（ENEW.py、macd_v4.py）保持原样，属历史遗留不修。`evolution_config_efuture_short.json` 的 `strategy_path` 为 `user_data/strategies/EfutureShort.py`，需手动改为 `user_data/strategies/efuture-short/EfutureShort.py`。

- [ ] **Step 3: 全仓复扫遗漏**

```bash
grep -rn "strategies/Efuturelong\|strategies/best_strategies\|strategies/autoresearch0\|freaiStr\|TopStr\|originalStratergy\|config_EfutureIter\|config_opt_loop" \
  scripts/ user_data/autoresearch/ user_data/configs/ CLAUDE.md 2>/dev/null | grep -v ".pyc"
```
预期：无输出。有输出则逐一修正。

- [ ] **Step 4: 循环脚本状态可读**

```bash
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path('user_data/strategies/efuture-long-kelvin/_loop_state.json').read_text())
print('loop state ok, keys:', list(s)[:5])
"
```
预期：打印 keys，无 FileNotFoundError。

- [ ] **Step 5: Commit**

```bash
git add -A scripts/ user_data/autoresearch/
git commit -m "fix: 更新循环脚本与 evolution_config 的 strategies 路径引用"
```

---

### Task 9: 文档落盘（README + CLAUDE.md + LINEAGE）

**Files:**
- Create: `user_data/README.md`、`user_data/configs/README.md`
- Create: `user_data/strategies/{efuture-iter,efuture-long,efuture-long-kelvin,efuture-short,enew,freqai-v6}/LINEAGE.md`
- Modify: `CLAUDE.md`（在 "Autoresearch 进化系统运维记录" 节前插入新节）

- [ ] **Step 1: 写 user_data/README.md**

内容为 spec 的"目标目录结构 + 命名规范总表 + 两条核心原则 + 产物三层保留策略"四节，从 spec 原文复制，头部加一行：`> 本文档是 user_data 的组织规范，设计依据见 docs/superpowers/specs/2026-07-25-userdata-reorganization-design.md`。

- [ ] **Step 2: 写 configs/README.md**

```markdown
# configs 清单

| 文件 | 环境 | 策略线 | 说明 | 最后验证 |
|---|---|---|---|---|
| live/EfutureIter.json | 实盘 dry_run=false | efuture-iter | 真身（2026-07-25 裁定） | 2026-07-25 |
| dryrun/EfutureIter.json | 模拟盘 | efuture-iter | 原 config_EfutureIter_kelvin.json | 2026-07-25 |
| backtest/EfutureLongKelvin.json | 回测/循环 | efuture-long-kelvin | 原 config_opt_loop.json | 2026-07-25 |
| backtest/FreqaiV6.json | 回测 | freqai-v6 | 原 freaiStr/config_freqai_v6.json | 2026-07-25 |

规则：环境×策略线最多一个主 config；密钥一律在 secrets.local.json（gitignored），启动用多个 -c 合并；live/ 修改单独 commit 并写明原因。
```

- [ ] **Step 3: 写 6 个 LINEAGE.md**

efuture-iter/LINEAGE.md（内容最完整，其余线同构）：

```markdown
# efuture-iter 谱系

| 版本 | 来源 | OOS 结果 |
|---|---|---|
| AutoResearch_iter0002_E0010_E0014_E0033 | autoresearch0515 实验链 E0010→E0014→E0033 | 训练 +429.5%/测试 +42.63%（2026-05-25 审查，custom_stoploss bug 未修复，见审查报告） |
| EfutureIter | E0033 改名 + 参数迭代 | 实盘运行中（2026-07 起） |
```

efuture-long-kelvin/LINEAGE.md 从 `EfutureLongKelvin_optimization_report.md` 提取 v1→v12_2x 链；其余线按目录内文件如实填写已知谱系。

- [ ] **Step 4: CLAUDE.md 插入 "user_data 组织规范" 节**

在 `## Autoresearch 进化系统运维记录` 之前插入：

```markdown
## user_data 组织规范

策略按线组织：`strategies/<线名>/`（efuture-iter、efuture-long、efuture-long-kelvin、efuture-short、enew、freqai-v6），实验产物在 `strategies/experiments/`。config 按环境三分：`configs/live|dryrun|backtest/`，密钥在 `configs/secrets.local.json`（不入库，启动用多 -c 合并）。

命名：文件名只表达"是什么"——代际 `_v<N>` 表达版本，目录表达状态，实验号仅在 autoresearch 产物中存在。每线谱系见各目录 LINEAGE.md，完整规范见 user_data/README.md。
```

- [ ] **Step 5: Commit**

```bash
git add user_data/README.md user_data/configs/README.md user_data/strategies/*/LINEAGE.md CLAUDE.md
git commit -m "docs: user_data 组织规范、config 清单、六线 LINEAGE 谱系"
```

---

### Task 10: 实盘 EfutureIter 路径切换（需用户在场）

**Files:**
- Create: `user_data/livebot/efuture-iter_v1_0725/`（部署快照）

- [ ] **Step 1: 找到实盘启动方式**

```bash
ps aux | grep "freqtrade trade" | grep -v grep
```
记录当前进程的 `-c` 参数与 cwd，向用户报告。

- [ ] **Step 2: 用户确认停机窗口后，用新路径重启**

```bash
.venv/bin/freqtrade trade --config user_data/configs/live/EfutureIter.json --config user_data/configs/secrets.local.json
```

- [ ] **Step 3: 观察验证**

验证点：日志无 strategy load error；Telegram 通知正常（secrets 合并生效）；`/status` 可查。至少观察一个完整 populate 周期。

- [ ] **Step 4: 建部署快照**

```bash
mkdir -p user_data/livebot/efuture-iter_v1_0725
cp user_data/configs/live/EfutureIter.json user_data/livebot/efuture-iter_v1_0725/
cp user_data/strategies/efuture-iter/EfutureIter.py user_data/strategies/efuture-iter/EfutureIter.json user_data/livebot/efuture-iter_v1_0725/
echo "commit: $(git rev-parse HEAD)" > user_data/livebot/efuture-iter_v1_0725/SOURCE.md
```

- [ ] **Step 5: Commit**

```bash
git add user_data/livebot/efuture-iter_v1_0725/
git commit -m "chore(livebot): efuture-iter 新结构首次部署快照"
```

---

### Task 11: 磁盘清理（195G + 已结束实验）

**Files:**
- Delete: `user_data/hyperopt_results/` 中除每线最新一轮外的 `.fthypt*`
- Delete: `user_data/autoresearch/results/` 中结束超 30 天的实验目录

- [ ] **Step 1: 前置检查——参数存档完整**

```bash
ls user_data/archive/params-archive/*.json | wc -l
```
预期：与 Task 3 导出数量一致。为 0 则中止本任务。

- [ ] **Step 2: 生成删除清单（dry-run，用户确认）**

```bash
ls -lt user_data/hyperopt_results/*.fthypt* | head -20
```
按策略线人工确认每线保留最新 1 个文件，列出删除清单交用户确认。

- [ ] **Step 3: 用户确认后删除，复验磁盘**

```bash
du -sh user_data/hyperopt_results/ user_data/autoresearch/results/
df -h / | tail -1
```

- [ ] **Step 4: autoresearch/results 结束实验清理**

```bash
find user_data/autoresearch/results -maxdepth 1 -type d -mtime +30 -print
```
逐个目录确认对应 evolution_config 不再活跃后删除。

- [ ] **Step 5: Commit**

```bash
git add -A user_data/
git commit -m "chore: 清理历史 hyperopt/实验产物，参数已存档 params-archive"
```

---

### Task 12: 终验

- [ ] **Step 1: 每条线 backtest 冒烟（短窗口）**

```bash
.venv/bin/freqtrade backtesting --strategy-path user_data/strategies/efuture-long-kelvin --strategy EfutureLongKelvin_v12_2x --config user_data/configs/backtest/EfutureLongKelvin.json --timerange 20260601-20260615 2>&1 | tail -5
```
预期：正常产出回测结果，无加载错误。efuture-iter 线用 configs/backtest/EfutureIter.lineage.json 同理验证。

- [ ] **Step 2: 全仓最终扫描**

```bash
grep -rn "freaiStr\|TopStr\|originalStratergy\|Efuturelong/\|best_strategies\|config_EfutureIter\|config_opt_loop" scripts/ user_data/configs/ user_data/autoresearch/*.json user_data/autoresearch/*.py CLAUDE.md 2>/dev/null | grep -v ".pyc"
```
预期：无输出。

- [ ] **Step 3: git 状态与体积复核**

```bash
git status --porcelain
git count-objects -vH | grep size-pack
```
预期：工作区干净；size-pack 无异常膨胀（产物未入库）。

- [ ] **Step 4: 收尾 commit**

```bash
git add -A && git commit -m "chore: user_data 重组终验通过" --allow-empty
```

---

## Self-Review 记录

- Spec 覆盖：目录结构(T5-T7)、版本跟踪(T1,T9 LINEAGE)、config 规则(T2,T6)、产物三层(T3,T11)、命名规范落盘(T9)、livebot 规则(T10)、迁移 10 步(T1-T12 对应)、实盘保护(T10 置后)——全覆盖
- spec 中 `scripts/clean_experiment.py`（实验收尾自动化）未单列任务：属 autoresearch 流程优化，不在重组关键路径，避免范围蔓延，后续单独立项
- 一致性：configs 路径 `user_data/configs/{live,dryrun,backtest}/` 在 T2/T6/T9/T10 间一致；线目录名在 T5/T8/T9 间一致
- stoploss bug（EfutureIter.py:322-326）按用户决策单独立项，不在本计划
