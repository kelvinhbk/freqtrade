# fre-livebot-server-deploy v2 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已批准的设计（`docs/superpowers/specs/2026-08-02-livebot-server-deploy-v2-design.md`）升级部署技能：命名规范 v2、700m 内存限额、部署前哈希冲突校验、本地备份留档，并迁移服务器现有 bot。

**Architecture:** 单一 python 脚本 `build_deploy_package.py` 负责全部确定性工作（合并 config、生成 compose service、冲突校验）；SKILL.md 编排 SSH 流程；SSH 侧只执行简单命令。新增纯函数均以 pytest 覆盖。

**Tech Stack:** Python 3（PyYAML，用仓库 `.venv/bin/python` 运行）、pytest、docker compose v2、ssh/scp。

## Global Constraints

- 脚本必须用 `<项目根>/.venv/bin/python` 运行（系统 python3 无 PyYAML）
- bot 名推导：快照目录名去末尾 `_\d{4}` 日期后缀；末尾非 4 位数字时用整个目录名
- 内存限额固定 `700m`；端口从 8080 起 +10 分配
- 合并 config 含明文密钥：chmod 600，且 `livebot/*/deploy/` 必须 gitignore
- 服务器 config 属主必须是容器用户（用 `chown --reference` 对齐现有 config）
- telegram token 冲突 = 中止（exit 3）；exchange key 冲突 = 警告（exit 4），加 `--allow-exchange-key-conflict` 才继续
- 策略 .py/.json 文件名保持类名基名不变（freqtrade 约束）
- git 提交步骤需用户同意后执行（用户全局规则：不自动提交）

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `~/.claude/skills/fre-livebot-server-deploy/scripts/build_deploy_package.py` | 构建部署包 + 冲突校验 | 修改 |
| `~/.claude/skills/fre-livebot-server-deploy/tests/test_build_deploy_package.py` | 纯函数单元测试 | 新建 |
| `~/.claude/skills/fre-livebot-server-deploy/SKILL.md` | 流程编排文档 | 修改 |
| `/Users/kelvin/projects/freqtrade/.gitignore` | 豁免 deploy 备份目录 | 修改（+1 行） |
| 服务器 ft-live | 现有 bot 迁移到新命名 | 执行 runbook |

---

### Task 1: 新增纯函数的失败测试

**Files:**
- Create: `~/.claude/skills/fre-livebot-server-deploy/tests/test_build_deploy_package.py`

**Interfaces:**
- Consumes: 现有 `build_deploy_package.py`（当前 `build_service(strategy, port, mmdd)` 签名将被 Task 2 改为 `build_service(bot_name, strategy, port)`）
- Produces: 测试锁定的新接口——`derive_bot_name(snapshot_name) -> str`、`build_service(bot_name, strategy, port) -> dict`、`hash_secret(value) -> str`、`check_conflicts(config, server_hashes, exclude: set) -> list[str]`

- [ ] **Step 1: 写测试文件**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_deploy_package as bdp


def test_derive_bot_name_strips_date():
    assert bdp.derive_bot_name("efuture-long-kelvin_v12_2x_0729") == "efuture-long-kelvin_v12_2x"


def test_derive_bot_name_no_date_fallback():
    assert bdp.derive_bot_name("my-bot_v3") == "my-bot_v3"


def test_build_service_uses_bot_name_and_limit():
    svc = bdp.build_service("efuture-long-kelvin_v12_2x", "EfutureLongKelvin_v12_2x_lock", 8090)
    assert svc["container_name"] == "efuture-long-kelvin_v12_2x"
    assert svc["mem_limit"] == "700m"
    assert "logs/efuture-long-kelvin_v12_2x.log" in svc["command"]
    assert "dbs/efuture-long-kelvin_v12_2x.sqlite" in svc["command"]
    assert "config_efuture-long-kelvin_v12_2x.json" in svc["command"]
    assert "--strategy EfutureLongKelvin_v12_2x_lock" in svc["command"]
    assert svc["ports"] == ["0.0.0.0:8090:8090"]


def test_check_conflicts_telegram_duplicate():
    config = {"telegram": {"token": "abc"}, "exchange": {"key": "k1"}}
    server = {"/x/config_other.json": {"telegram_token": bdp.hash_secret("abc"),
                                       "exchange_key": bdp.hash_secret("k9")}}
    assert bdp.check_conflicts(config, server, {"config_me.json"}) == ["telegram:/x/config_other.json"]


def test_check_conflicts_excludes_own_and_listed():
    config = {"telegram": {"token": "abc"}, "exchange": {"key": "k1"}}
    server = {"/x/config_old.json": {"telegram_token": bdp.hash_secret("abc"),
                                     "exchange_key": bdp.hash_secret("k1")}}
    assert bdp.check_conflicts(config, server, {"config_me.json", "config_old.json"}) == []


def test_check_conflicts_skips_empty():
    config = {"telegram": {"token": ""}, "exchange": {"key": ""}}
    server = {"/x/config_o.json": {"telegram_token": bdp.hash_secret(""),
                                   "exchange_key": bdp.hash_secret("")}}
    assert bdp.check_conflicts(config, server, {"config_me.json"}) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ~/.claude/skills/fre-livebot-server-deploy && /Users/kelvin/projects/freqtrade/.venv/bin/python -m pytest tests/ -v`
Expected: FAIL — `AttributeError: module 'build_deploy_package' has no attribute 'derive_bot_name'`

---

### Task 2: 脚本实现（bot 名推导、限额、冲突校验、备份目录默认输出）

**Files:**
- Modify: `~/.claude/skills/fre-livebot-server-deploy/scripts/build_deploy_package.py`

**Interfaces:**
- Consumes: Task 1 的测试
- Produces: 同 Task 1 接口；CLI 变更：`--server-hashes <path>`（可选，服务器哈希 JSON）、`--exclude-config <文件名...>`（可选，冲突校验排除项）、`--allow-exchange-key-conflict`（开关）、`--out` 改为可选（默认 `livebot/<快照>/deploy/<YYYYMMDD-HHMM>/`）

- [ ] **Step 1: 新增三个纯函数 + 改 build_service**

文件头部 import 增加 `hashlib`。新增/替换以下函数：

```python
def derive_bot_name(snapshot_name: str) -> str:
    """'efuture-long-kelvin_v12_2x_0729' -> 'efuture-long-kelvin_v12_2x'；末尾非 _MMDD 则用全名。"""
    return re.sub(r"_\d{4}$", "", snapshot_name)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def check_conflicts(config: dict, server_hashes: dict, exclude: set) -> list:
    """比对合并后 config 与服务器在跑 bot 的 token/key 哈希。空值跳过；exclude 为排除的 config 文件名集合。
    返回冲突描述列表，如 ['telegram:/path/config_x.json']。"""
    problems = []
    token = config.get("telegram", {}).get("token", "")
    key = config.get("exchange", {}).get("key", "")
    token_h = hash_secret(token) if token else None
    key_h = hash_secret(key) if key else None
    for path, hashes in server_hashes.items():
        if Path(path).name in exclude:
            continue
        if token_h and hashes.get("telegram_token") == token_h:
            problems.append(f"telegram:{path}")
        if key_h and hashes.get("exchange_key") == key_h:
            problems.append(f"exchange:{path}")
    return problems


def build_service(bot_name: str, strategy: str, port: int) -> dict:
    return {
        "image": COMPOSE_IMAGE,
        "restart": "unless-stopped",
        "container_name": bot_name,
        "mem_limit": "700m",
        "volumes": ["./user_data:/freqtrade/user_data"],
        "ports": [f"0.0.0.0:{port}:{port}"],
        "command": (
            "trade "
            f"--logfile /freqtrade/user_data/logs/{bot_name}.log "
            f"--db-url sqlite:////freqtrade/user_data/dbs/{bot_name}.sqlite "
            f"--config /freqtrade/user_data/config_{bot_name}.json "
            f"--strategy {strategy}"
        ),
    }
```

- [ ] **Step 2: 改 main()**

argparse 变更：

```python
    parser.add_argument("--server-hashes", default=None,
                        help="JSON file: {server_config_path: {telegram_token: hash, exchange_key: hash}}")
    parser.add_argument("--exclude-config", nargs="*", default=[],
                        help="config filenames excluded from conflict check (e.g. old-named config being migrated)")
    parser.add_argument("--allow-exchange-key-conflict", action="store_true")
    parser.add_argument("--out", default=None,
                        help="staging output dir; default livebot/<snapshot>/deploy/<YYYYMMDD-HHMM>/")
```

main() 主体变更（在合并 config 之后、写文件之前插入冲突校验；service key 与文件命名改用 bot_name；--out 默认值逻辑）：

```python
    dry_run = args.dry_run == "true"
    config = adapt_config_for_server(config, args.port, dry_run)

    bot_name = derive_bot_name(snapshot_dir.name)
    own_config_filename = f"config_{bot_name}.json"

    # --- conflict checks ---
    conflict_report = {"checked": False, "telegram_conflict": None, "exchange_conflict": None}
    if args.server_hashes:
        with open(args.server_hashes) as f:
            server_hashes = json.load(f)
        problems = check_conflicts(config, server_hashes,
                                   {own_config_filename, *args.exclude_config})
        conflict_report["checked"] = True
        telegram_conflicts = [p for p in problems if p.startswith("telegram:")]
        exchange_conflicts = [p for p in problems if p.startswith("exchange:")]
        if telegram_conflicts:
            sys.exit(f"telegram token conflict with running bot(s): {telegram_conflicts} — "
                     "create a dedicated bot via @BotFather and update the telegram fragment")
        if exchange_conflicts and not args.allow_exchange_key_conflict:
            sys.exit(f"exchange key shared with running bot(s): {exchange_conflicts} — "
                     "one account driven by two bots corrupts position tracking; "
                     "re-run with --allow-exchange-key-conflict if intentional")
        conflict_report["exchange_conflict"] = exchange_conflicts or None

    # --- merge docker-compose ---
    # （compose 加载逻辑不变；service key 与 replaced 判定改用 bot_name）
    replaced = bot_name in compose["services"]
    compose["services"][bot_name] = build_service(bot_name, strategy, args.port)

    # --- output dir default ---
    if args.out:
        out_dir = Path(args.out).resolve()
    else:
        out_dir = snapshot_dir / "deploy" / datetime.now().strftime("%Y%m%d-%H%M")
```

写文件部分：`config_out` 文件名改为 `config_{bot_name}.json`；manifest 增加字段 `"bot_name": bot_name`、`"secrets": args.secrets`、`"conflict_report": conflict_report`，`db` 字段改为 `f"{bot_name}.sqlite"`，删除 `mmdd` 变量（不再使用）。

- [ ] **Step 3: 运行测试确认通过**

Run: `cd ~/.claude/skills/fre-livebot-server-deploy && /Users/kelvin/projects/freqtrade/.venv/bin/python -m pytest tests/ -v`
Expected: 6 passed

- [ ] **Step 4: 用真实快照冒烟（不部署）**

```bash
scp ft-live:/root/live/ft_userdata/docker-compose.yml /tmp/v2-smoke-compose.yml
cd /Users/kelvin/projects/freqtrade && .venv/bin/python ~/.claude/skills/fre-livebot-server-deploy/scripts/build_deploy_package.py \
  --snapshot livebot/efuture-long-kelvin_v12_2x_0729 \
  --secrets binance.main telegram.efuture_kelvin apiserver.efuture_kelvin \
  --port 8090 --dry-run true \
  --server-compose /tmp/v2-smoke-compose.yml
```

Expected: 输出 manifest 含 `"bot_name": "efuture-long-kelvin_v12_2x"`，产物写入 `livebot/efuture-long-kelvin_v12_2x_0729/deploy/<时间戳>/`，config 权限 600，compose service 含 `mem_limit: 700m`。验证后**删除该 smoke 产物目录**（避免与 Task 5 迁移产物混淆）。

---

### Task 3: .gitignore 豁免备份目录

**Files:**
- Modify: `/Users/kelvin/projects/freqtrade/.gitignore`（在 `!user_data/configs/secrets/README.md` 行后追加）

- [ ] **Step 1: 追加规则**

```
livebot/*/deploy/
```

- [ ] **Step 2: 验证豁免生效**

Run: `cd /Users/kelvin/projects/freqtrade && git check-ignore -v livebot/efuture-long-kelvin_v12_2x_0729/deploy/test/config_x.json`
Expected: 输出匹配 `livebot/*/deploy/` 规则的行（exit 0）

同时确认 `git status livebot/` 干净（Task 2 smoke 产物已删）。

---

### Task 4: SKILL.md 更新

**Files:**
- Modify: `~/.claude/skills/fre-livebot-server-deploy/SKILL.md`

**Interfaces:**
- Consumes: Task 2 的 CLI（`--server-hashes`、`--exclude-config`、`--allow-exchange-key-conflict`、--out 默认）
- Produces: 部署流程 v2（后续会话按此执行）

- [ ] **Step 1: 替换"命名规范"与流程相关段落**

要点（保持原文风格，逐项落实）：

1. 固定环境表：config 落点改 `/root/live/ft_userdata/user_data/config_<bot名>.json`；新增"bot 名 = 快照目录名去末尾日期后缀"
2. 新增命名规范 v2 表（照抄 spec 的表：service/config/db/log 命名 + 策略 .py/.json 不变）
3. 流程步骤 1 勘察追加——提取服务器哈希供冲突校验：

```bash
ssh ft-live 'python3 -c "
import json, hashlib, glob
out = {}
for p in glob.glob(\"/root/live/ft_userdata/user_data/config_*.json\"):
    c = json.load(open(p))
    out[p] = {
      \"telegram_token\": hashlib.sha256(c.get(\"telegram\", {}).get(\"token\", \"\").encode()).hexdigest()[:16],
      \"exchange_key\": hashlib.sha256(c.get(\"exchange\", {}).get(\"key\", \"\").encode()).hexdigest()[:16],
    }
print(json.dumps(out))
"' > /tmp/ft_hashes_<快照名>.json
```

（说明：只传哈希不传原值。）

4. 步骤 2 更新：staging 默认 `livebot/<快照名>/deploy/<YYYYMMDD-HHMM>/`（不再用 /tmp，不再删除）；构建命令加 `--server-hashes /tmp/ft_hashes_<快照名>.json`；说明 exit 3=telegram 冲突中止、exit 4=exchange 冲突需用户确认后加 `--allow-exchange-key-conflict` 重跑
5. 步骤 5 上传后、`docker compose up` 前插入：

```bash
ssh ft-live 'cd /root/live/ft_userdata && docker compose config -q && echo COMPOSE_OK'
```

失败则中止，用步骤 4 的备份回滚 compose。
6. 安全红线更新：备份目录已 gitignore；哈希比对不打印原值
7. 备注更新：mem_limit 700m 已内置；多 bot 注意服务器总内存 1.7GB（2 个接近上限，3 个需升配）

- [ ] **Step 2: 通读校验**

确认全文无残留 v1 命名（`config_<类名>_live.json`、`_live_<MMDD>.sqlite` 只出现在"回滚/迁移"语境）。

---

### Task 5: 服务器现有 bot 迁移到新命名

**Files:**
- 服务器 ft-live：`/root/live/ft_userdata/`（compose、config、db 改名）
- 本地：`livebot/efuture-long-kelvin_v12_2x_0729/deploy/<时间戳>/`（迁移产物留档）

**Interfaces:**
- Consumes: Task 2 脚本、Task 4 流程
- Produces: 服务器上以 `efuture-long-kelvin_v12_2x` 命名的运行中实盘 bot

前提：当前在跑的 `EfutureLongKelvin_v12_2x_lock`（dry_run=false 实盘，端口 8090）；`EfutureIter` 已停止，**不动**。

- [ ] **Step 1: 备份 + 下载 compose + 提取哈希**

```bash
ssh ft-live 'cd /root/live/ft_userdata && cp docker-compose.yml docker-compose-backup-$(date +%Y%m%d-%H%M).yml'
mkdir -p /tmp/ft-migrate && scp ft-live:/root/live/ft_userdata/docker-compose.yml /tmp/ft-migrate/server-compose.yml
ssh ft-live 'python3 -c "
import json, hashlib, glob
out = {}
for p in glob.glob(\"/root/live/ft_userdata/user_data/config_*.json\"):
    c = json.load(open(p))
    out[p] = {
      \"telegram_token\": hashlib.sha256(c.get(\"telegram\", {}).get(\"token\", \"\").encode()).hexdigest()[:16],
      \"exchange_key\": hashlib.sha256(c.get(\"exchange\", {}).get(\"key\", \"\").encode()).hexdigest()[:16],
    }
print(json.dumps(out))
"' > /tmp/ft-migrate/server-hashes.json
```

- [ ] **Step 2: 构建迁移包**

```bash
cd /Users/kelvin/projects/freqtrade && .venv/bin/python ~/.claude/skills/fre-livebot-server-deploy/scripts/build_deploy_package.py \
  --snapshot livebot/efuture-long-kelvin_v12_2x_0729 \
  --secrets binance.main telegram.efuture_kelvin apiserver.efuture_kelvin \
  --port 8090 --dry-run false \
  --server-compose /tmp/ft-migrate/server-compose.yml \
  --remove-service EfutureLongKelvin_v12_2x_lock \
  --server-hashes /tmp/ft-migrate/server-hashes.json \
  --exclude-config config_EfutureLongKelvin_v12_2x_lock_live.json
```

Expected: manifest `bot_name=efuture-long-kelvin_v12_2x`、`dry_run=false`、service 含 700m 限额、无冲突退出。产物在 `livebot/efuture-long-kelvin_v12_2x_0729/deploy/<时间戳>/`。

- [ ] **Step 3: 上传 + 服务器侧改名**

```bash
D=$(ls -d livebot/efuture-long-kelvin_v12_2x_0729/deploy/*/ | tail -1)
scp "$D/config_efuture-long-kelvin_v12_2x.json" ft-live:/root/live/ft_userdata/user_data/
scp "$D/docker-compose.yml" ft-live:/root/live/ft_userdata/docker-compose.yml
ssh ft-live 'cd /root/live/ft_userdata && \
  chown --reference=user_data/config_EfutureIter_live.json user_data/config_efuture-long-kelvin_v12_2x.json && \
  chmod 600 user_data/config_efuture-long-kelvin_v12_2x.json && \
  mv user_data/dbs/EfutureLongKelvin_v12_2x_lock_live_0802.sqlite user_data/dbs/efuture-long-kelvin_v12_2x.sqlite && \
  docker compose config -q && echo COMPOSE_OK'
```

- [ ] **Step 4: 停旧起新**

```bash
ssh ft-live 'cd /root/live/ft_userdata && \
  docker stop EfutureLongKelvin_v12_2x_lock && docker rm EfutureLongKelvin_v12_2x_lock && \
  docker compose up -d efuture-long-kelvin_v12_2x'
```

- [ ] **Step 5: 验证**

```bash
ssh ft-live 'sleep 45; docker ps --format "{{.Names}}\t{{.Status}}"; \
  docker inspect -f "mem_limit={{.HostConfig.Memory}}" efuture-long-kelvin_v12_2x; \
  docker logs --since 2m efuture-long-kelvin_v12_2x 2>&1 | grep -E "Runmode|RUNNING|ERROR|Conflict" | tail -5'
```

Expected: 容器 Up；`mem_limit=734003200`（700m）；日志 `Runmode set to live` + heartbeat RUNNING；无 ERROR/Conflict；交易历史连续（db 是改名的旧文件，Telegram `/status` 可见既有记录）。

- [ ] **Step 6: 清理旧 config + 删除临时目录**

验证通过后：

```bash
ssh ft-live 'rm /root/live/ft_userdata/user_data/config_EfutureLongKelvin_v12_2x_lock_live.json /root/live/ft_userdata/user_data/config_EfutureLongKelvin_v12_2x_lock_live.json.bak-dryrun'
rm -rf /tmp/ft-migrate
```

（旧 config 含明文密钥，确认新 config 工作正常后删除，避免服务器上留多份密钥副本。）

---

### Task 6: 收尾提交（需用户同意）

- [ ] **Step 1: 向用户汇报全部变更，征得同意后提交**

```bash
cd /Users/kelvin/projects/freqtrade
git add .gitignore docs/superpowers/specs/2026-08-02-livebot-server-deploy-v2-design.md docs/superpowers/plans/2026-08-02-livebot-server-deploy-v2.md
git commit -m "docs(livebot-deploy): v2 设计——命名规范/内存限额/冲突校验/本地备份 + gitignore 豁免 deploy 目录"
```

（技能目录 ~/.claude/skills/ 不在本仓库，不提交。）

---

## Self-Review 记录

- Spec 覆盖：命名 v2→Task 2/4；700m 限额→Task 2（build_service）+Task 5 验证；本地备份→Task 2（--out 默认）+Task 3；冲突校验四项→Task 1/2（telegram/key/manifest）+Task 4（端口沿用现有说明）；迁移→Task 5；YAGNI 三项未纳入任何任务 ✓
- 占位符：无（所有代码与命令为完整可执行内容）✓
- 类型一致：`check_conflicts(config, server_hashes, exclude: set)` 在测试/实现/SKILL 描述中一致；`build_service(bot_name, strategy, port)` 一致；exit 3/4 语义在 Task 2 实现与 Task 4 文档一致 ✓
