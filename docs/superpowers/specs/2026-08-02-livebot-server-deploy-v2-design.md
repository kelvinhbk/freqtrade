# fre-livebot-server-deploy v2 设计

- 日期：2026-08-02
- 状态：已与用户确认方案，待实现
- 技能位置：`~/.claude/skills/fre-livebot-server-deploy/`

## 背景

v1 技能（2026-08-02 上午创建）完成首次真实部署（efuture-long-kelvin_v12_2x_0729 → ft-live），暴露四类问题：

1. config 上传后属主 root + 600 → 容器读不到 → 崩溃循环 → 1.7GB 小 VPS 被拖垮（sshd 失联约 30 分钟，实盘 EfutureIter 受殃及）——v1.1 已修 chown 问题，但缺资源隔离
2. 新旧 bot 共用同一 telegram token → getUpdates 冲突，两边通知不可靠——缺部署前校验
3. 生成文件（含明文密钥的合并 config）只在 /tmp 暂存，部署完删除，本地无留档
4. 命名冗长（`config_EfutureLongKelvin_v12_2x_lock_live.json`），且 db 名带部署日期导致重部署交易历史断档

服务器硬件：1.7GB RAM / 2 核（腾讯云轻量）。两个 freqtrade bot 已接近容量上限，3 个以上需升配。

## 决策汇总（用户已确认）

| 议题 | 决策 |
|---|---|
| 多 bot 共存架构 | 方案 A：单 compose 多 service + 加固（不迁移到独立目录） |
| 本地备份位置 | `livebot/<快照名>/deploy/<YYYYMMDD-HHMM>/`，gitignored |
| 命名规范 | 见下表 v2（含策略线名+版本号，去冗长类名与 _live 后缀） |
| 部署前校验 | telegram token 冲突中止、交易所 key 冲突警告、端口自动分配、manifest 登记片段 |
| 容器内存限额 | 每 service 700m |
| 现有 bot | 立即迁移到新命名（EfutureIter 不动） |

## 命名规范 v2

推导规则：快照目录名 `<线名>_<版本>_<MMDD>` 去掉日期后缀 = bot 名 `<线名>_<版本>`。
示例：`efuture-long-kelvin_v12_2x_0729` → bot 名 `efuture-long-kelvin_v12_2x`。
兜底：目录名末尾不是 4 位数字日期时，直接用整个目录名作为 bot 名。

| 项 | v1 | v2 |
|---|---|---|
| service/容器名 | `EfutureLongKelvin_v12_2x_lock` | `efuture-long-kelvin_v12_2x` |
| config | `config_<类名>_live.json` | `config_efuture-long-kelvin_v12_2x.json` |
| db | `<类名>_live_<MMDD>.sqlite`（每次部署变） | `efuture-long-kelvin_v12_2x.sqlite`（固定，重部署历史连续） |
| log | `<类名>_freqtrade.log` | `efuture-long-kelvin_v12_2x.log` |
| 策略 .py/.json | 类名基名 | 不变（freqtrade 约束：参数文件与策略文件同基名） |

## 共存加固（方案 A 落地）

1. 每个 service 增加：
   ```yaml
   deploy:
     resources:
       limits:
         memory: 700m
   ```
   单 bot 失控（如崩溃循环）最多占 700m，系统与其他 bot 存活。
2. 上传 compose 后、up 之前执行 `docker compose config -q` 校验语法，失败则中止并提示回滚。
3. 端口自动分配保留（8080 起 +10，扫描 compose + `docker ps`）。

## 本地备份

- staging 目录从 `/tmp/ft_deploy_<快照名>/` 改为 `livebot/<快照名>/deploy/<YYYYMMDD-HHMM>/`，构建即留档，部署完不再删除。
- 内容：合并 config（chmod 600）、docker-compose.yml、deploy_manifest.json、策略 .py/.json 副本。
- `.gitignore` 新增：`livebot/*/deploy/`（合并 config 含明文密钥，绝不入库）。

## 部署前防错校验（脚本新增）

构建包之前，脚本经 ssh 在服务器上提取各在跑 bot config 的 `telegram.token` 与 `exchange.key` 的 SHA256 哈希（不传输原值），本地比对：

1. telegram token 与任一在跑 bot 重复 → **中止**，提示换专属 token（@BotFather）
2. exchange key 与任一在跑 bot 重复 → **警告**，需用户确认才继续（一账号多 bot 持仓互相干扰）
3. 端口冲突 → 自动分配下一空闲端口（现有逻辑）
4. `deploy_manifest.json` 登记：bot 名、端口、使用的 secrets 片段名、部署时间、冲突校验结果

## 现有部署迁移

服务器上正在实盘的 `EfutureLongKelvin_v12_2x_lock` 按 v2 规范迁移（EfutureIter 保持停止状态、不动）：

1. compose service 改名 `efuture-long-kelvin_v12_2x` + 加 700m 限额
2. `config_EfutureLongKelvin_v12_2x_lock_live.json` → `config_efuture-long-kelvin_v12_2x.json`（属主 lighthouse、600 不变）
3. db 文件 `dbs/EfutureLongKelvin_v12_2x_lock_live_0802.sqlite` → `dbs/efuture-long-kelvin_v12_2x.sqlite`（改名保留历史）
4. log 文件名随 service 定义自然切换
5. 流程：备份 → 改 compose/config/db → `docker compose config -q` → stop 旧容器 + rm → up 新 service → 验证 RUNNING

## 改动清单

| 文件 | 改动 |
|---|---|
| `~/.claude/skills/fre-livebot-server-deploy/scripts/build_deploy_package.py` | bot 名推导（去日期后缀）、service 加内存限额、部署前哈希冲突校验（新增 ssh 取哈希步骤）、输出目录改为 livebot/<快照>/deploy/<时间戳>/、compose 语法校验提示 |
| `~/.claude/skills/fre-livebot-server-deploy/SKILL.md` | 流程更新（备份目录、校验步骤、compose config 校验）、命名表 v2、多 bot 注意事项 |
| `.gitignore` | +`livebot/*/deploy/` |
| 服务器 | 现有 bot 迁移（见上节） |

## 不做的事（YAGNI）

- 不做每 bot 独立目录架构（方案 B 已否决，bot 数量多到 A 撑不住时再议）
- 不做服务器侧 registry 文件（manifest + secrets/README.md 表格已够）
- 不做 telegram 代理配置（链路不稳定问题待观察，另行处理）
