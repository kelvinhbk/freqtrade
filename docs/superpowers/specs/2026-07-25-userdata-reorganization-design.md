# user_data 重组设计

日期：2026-07-25
状态：已获用户批准（方案 A）

## 背景

`user_data/` 当前存在六类混乱：

1. 策略平铺混杂：生产策略、7 个 autoresearch 实验目录（含空壳和疑似重复副本）、循环优化目录混在 `strategies/` 一层
2. config 分散在至少 5 个位置（user_data 根、freaiStr、TopStr、strategies/autoresearch0515-*、livebot 快照），且同一策略线有 4 个难以区分的 live 变体
3. 版本迭代无统一规则：代际命名（`_v4`）、实验号命名（`_Iter14`）、日期目录快照三种机制并存
4. 退役产物无归档位置：`originalStratergy/`、`backup/`、`before_live_bot/` 各自散落
5. autoresearch 产物无限堆积：`hyperopt_results` 195G、`autoresearch/results` 32G
6. git 跟踪不一致：autoresearch 框架代码入库，策略/config 部分入库

四条现役策略线（用户确认，全部保留）：

- EfutureIter（实盘运行中，dry_run=false）
- EfutureLongKelvin（循环优化中，已到 v12）
- autoresearch 系列实验（ENEW / macd_v4 / efuture）
- FreqaiExampleStrategy_v6

## 决策记录

| 决策点 | 结论 | 理由 |
|---|---|---|
| 组织方案 | 方案 A：按策略线组织 | 每条线的策略+参数+config 物理靠近，符合"优化一条线"的工作单元 |
| 版本跟踪 | user_data 全量入 git（同一仓库 kelvin 分支），大产物 .gitignore 排除 | 策略.py+参数.json+config 是逻辑整体，同 commit 才能完整回溯；已部分在这么做 |
| 整理力度 | 彻底重组目录 | 用户确认；实盘 bot 迁移排最后并做保护 |

## 目标目录结构

```
user_data/
├── strategies/
│   ├── efuture-iter/            # 实盘线
│   │   ├── EfutureIter.py
│   │   ├── EfutureIter.json
│   │   └── LINEAGE.md
│   ├── efuture-long-kelvin/     # 现 strategies/Efuturelong/
│   ├── efuture-long/            # EfutureLong 非 Kelvin 系列（v1/v2/v4），单独成线
│   ├── efuture-short/
│   ├── enew/
│   ├── freqai-v6/               # 现 freaiStr/
│   └── experiments/             # autoresearch 各实验策略目录（保留 autoresearch 原生命名）
├── configs/
│   ├── live/                    # dry_run=false；每策略线最多一个主 config
│   ├── dryrun/
│   ├── backtest/
│   └── README.md                # 每个 config 一行：用途、策略线、最后验证日期
├── autoresearch/                # 框架代码 + evolution_config_*.json 保留原位
├── archive/
│   ├── e0v1e/                   # 现 originalStratergy/（顺手修正拼写）
│   ├── backup/
│   ├── before-live-bot/
│   └── params-archive/          # 删除大产物前导出的 top-5 参数存档
├── livebot/                     # 部署快照（保留，加命名与 SOURCE.md 规则）
└── README.md                    # 组织规范与命名规则全文
```

## 版本迭代与连续性跟踪

1. 代际命名：`XxxYyy_v<N>.py` 只在策略逻辑结构性变化时升 N；纯参数调优不升版本
2. git 即历史：不保留 `v11_backup.py` 类文件；`_loop_state.json`、optimization_report 随代码提交
3. 每条策略线目录下放 `LINEAGE.md`，三列表格：版本 | 来源（父版本/实验号）| OOS 结果一句话
4. autoresearch 晋级规则：实验产物被采纳时才复制进策略线目录并改名升代；原实验目录保留在 experiments/ 或 autoresearch/results/ 下
5. TopStr/ 的处理（2026-07-25 核实结论）：其内容 `AutoResearch_iter0002_E0010_E0014_E0033` 与 `before_live_bot/Efuture_260520` 部署快照逐字节相同，且是当前实盘 `EfutureIter` 的直接祖先（改名后代）。TopStr 不作为独立策略线保留——E0033 的 .py/.json 与 `E0033_审查报告.md` 移入 `strategies/efuture-iter/` 作为 LINEAGE.md 的谱系起点记录，TopStr/ 目录撤销
6. **已知未修复缺陷（超出本次整理范围，需单独任务）**：E0033 审查报告（2026-05-25）指出的 `custom_stoploss` 公式错误（30-240 分钟阶段实际止损约 -0.83% 而非设计的 -1.65%）原样存在于实盘 `EfutureIter.py:322-326`。是否修复、何时修复由用户决策

## config 分类规则

1. 环境 × 策略线 = 最多一个"当前" config。EfutureIter 的 live 真身已确认为 `config_EfutureIter_live.json`（用户裁定 2026-07-25），其余 3 个变体（`_live_opt`/`_live_opt_noproxy`/`_kelvin`）核对差异后删除
2. proxy 等环境差异移到 shell 环境变量，不复制整个 config
3. config 内路径保持相对
4. `live/` 修改单独 commit；加 pre-commit 检查：`live/*.json` 必须 `dry_run=false`，`dryrun/` 必须 `dry_run=true`
5. evolution_config_*.json 留在 autoresearch/ 原位（配置跟着使用者走）

## livebot 部署快照规则

- 新实盘部署 = 新建 `livebot/<策略线>_v<N>_<MMDD>/`，从 configs/live/ + strategies/<线>/ 复制
- 复制后放 `SOURCE.md` 记录 git commit hash
- 旧快照不再改动；退役后整目录移入 archive/

## autoresearch 产物三层保留策略

| 层 | 内容 | 策略 |
|---|---|---|
| 结论层（进 git） | LINEAGE.md、被采纳版本 .py/.json、optimization_report、每轮最佳参数摘要 | 永久 |
| 证据层（留盘不进 git） | 活跃实验 results/、最近 1-2 轮 hyperopt 结果 | 实验活跃期间保留 |
| 过程层（定期删） | 已结束实验 results、历史 .fthypt、backtest 中间产物 | 导出摘要后删除 |

具体动作：

1. hyperopt_results 195G：先用脚本把每轮 top-5 参数导出为小 json 存 `archive/params-archive/`，然后每条线只保留最新一轮，其余删除
2. autoresearch/results 32G：实验超 30 天无迭代 → 导出摘要 → 删原始产物；活跃实验不动
3. 删除空壳 `strategies/autoresearch_efuture_short`；`autoresearch0515-1/2` 已确认完全相同（2026-07-25 diff 验证），删 0515-2；backup/ 与 git 历史核对后清理
4. 新增 `scripts/clean_experiment.py`：实验结束时自动导出摘要 + 提示可删路径，使清理成为流程一部分

总原则：git 存"结论"，磁盘只留"还会再用的证据"；回测/hyperopt 产物是数据和代码的函数，可重算，不囤积。

## 命名规范（写入 user_data/README.md 与 CLAUDE.md）

| 对象 | 规则 | 示例 | 反例 |
|---|---|---|---|
| 策略目录 | 小写连字符，策略线名 | `efuture-long-kelvin/` | `Efuturelong/`、`freaiStr/` |
| 策略文件 | 类名同名，代际 `_v<N>` | `EfutureLongKelvin_v12.py` | `EfutureShort_Iter14.py` |
| 参数文件 | 与策略同名 | `EfutureLongKelvin_v12.json` | `EfutureLong_v4_hyperopt.json` |
| config | `<策略线>.json`，变体点分后缀 | `EfutureIter.opt.json` | `config_EfutureIter_live_opt_noproxy.json` |
| 实验产物 | autoresearch 原生命名不动，晋级时改名 | `ENEW_E0036.py` | — |
| 部署快照 | `livebot/<策略线>_v<N>_<MMDD>/` | `livebot/efuture-iter_v3_0725/` | `livebot/20250202version/` |
| 归档 | `archive/<原用途>/` | `archive/backup/` | `originalStratergy/` |

核心原则：

1. 文件名只表达"是什么"，不表达状态/用途/环境——状态由目录表达，代际由 `_v<N>` 表达
2. 一个名字一处含义：类名 `EfutureIter` ↔ 线名 `efuture-iter` 机械对应

落盘位置：规则全文 → `user_data/README.md`；精简版 + 链接 → `CLAUDE.md` 新增 "user_data 组织规范" 一节（位于 Autoresearch 运维记录之前）；config 清单 → `configs/README.md`。

## 迁移执行步骤

```
1. git 快照      → user_data 全部 commit，工作区干净
2. 实盘保护      → EfutureIter 实盘迁移排最后，停机窗口内改路径+重启
3. .gitignore    → 补全（results/、hyperopt_results/、__pycache__、livebot/ 快照）；
                    验证 git status 无大文件
4. 导出存档      → 脚本导出 hyperopt 各轮 top-5 参数 → archive/params-archive/
5. 删除安全项    → 空壳目录、0515-1/2 重复目录（先 diff）、__pycache__
6. git mv 重组   → strategies/ 分线 → configs/ 集中 → archive/ 收退役（全程 git mv）
7. 改引用        → autoresearch 脚本、evolution_config、启动命令、scripts/*.py；
                    grep 全仓 "strategies/" 逐一核对
8. 写文档        → user_data/README.md + CLAUDE.md 规范节 + 各 LINEAGE.md 骨架
9. 清理磁盘      → hyperopt_results 195G、已结束实验 results/（第 4 步完成后）
10. 验证         → 每条线 backtest 冒烟（新路径可加载）；实盘切换后观察一个周期
```

风险控制：第 1、4 步是回滚保险；第 2 步保证实盘零意外；第 7 步用 grep 全量扫描而非凭记忆。
